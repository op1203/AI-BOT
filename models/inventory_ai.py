from odoo import models, fields, api, _
from odoo.tools import Markup
from datetime import datetime, timedelta
import logging
import requests
import json
import re

_logger = logging.getLogger(__name__)

class InventoryAIBot(models.Model):
    _name = 'inventory.ai.bot'
    _description = 'Inventory AI Bot'

    name = fields.Char(string='Name', required=True)
    description = fields.Text(string='Description')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('inactive', 'Inactive')
    ], string='Status', default='draft')


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def _message_post_after_hook(self, message, values_list):
        super()._message_post_after_hook(message, values_list)
        
        # Check if the message is in the AI channel
        ai_channel = self.env.ref('inventory_ai.channel_inventory_ai', raise_if_not_found=False)
        if ai_channel and self.id == ai_channel.id:
            # Avoid infinite loops (don't respond to own messages)
            bot_partner = self.env.ref('inventory_ai.partner_inventory_ai_bot', raise_if_not_found=False)
            if message.author_id != bot_partner:
                # 1. VISUAL DAILY REFRESH:
                # Check if we need a session banner for today
                today_str = fields.Date.context_today(self).strftime('%B %d, %Y')
                
                # Look for the last message in this channel
                last_msg = self.env['mail.message'].search([
                    ('res_id', '=', self.id),
                    ('model', '=', 'discuss.channel'),
                    ('id', '<', message.id)
                ], order='id desc', limit=1)
                
                # If day changed or first message ever, post a banner
                if not last_msg or last_msg.create_date.date() < fields.Date.today():
                    session_banner = (
                        f"<div style='text-align: center; margin: 20px 0; padding: 10px; "
                        f"background: #f8f9fa; border-radius: 8px; border: 1px solid #dee2e6;'>"
                        f"<strong style='color: #495057;'>🚀 NEW SESSION — {today_str}</strong><br/>"
                        f"<small style='color: #6c757d;'>Stateless Mode: History is not sent to AI for maximum performance.</small></div>"
                    )
                    self.with_context(mail_create_nosubscribe=True).message_post(
                        body=session_banner,
                        author_id=bot_partner.id if bot_partner else self.env.ref('base.partner_root').id,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment'
                    )
                
                # 2. Get AI Response
                self._get_ai_response_and_post(message)

    def _get_operational_context(self):
        """Returns raw data structure for the AI to interpret. No formatting here."""
        
        data = "[RAW DATA FOR ANALYSIS]\n"
        
        # 1. Stats
        try:
            products = self.env['product.product'].search([('active', '=', True)])
            product_count = len(products)
            total_qty_on_hand = sum(products.mapped('qty_available'))
            data += f"STATS: ActiveProducts={product_count}, TotalStock={total_qty_on_hand}\n"
        except Exception as e:
            _logger.error("Error calculating stats context: %s", str(e))

        # 2. Revenue (Current Month vs Last Month)
        if 'sale.order' in self.env or 'pos.order' in self.env:
            try:
                with self.env.cr.savepoint():
                    today = datetime.now()
                    start_current = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    last_month_end = start_current - timedelta(days=1)
                    start_last_month = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    
                    def get_revenue_breakdown(start, end):
                        s_total, s_count = 0.0, 0
                        p_total, p_count = 0.0, 0
                        
                        start_str = fields.Datetime.to_string(start)
                        end_str = fields.Datetime.to_string(end)

                        if 'sale.order' in self.env:
                            self.env.cr.execute("""
                                SELECT count(*), sum(amount_total) 
                                FROM sale_order 
                                WHERE state IN ('sale', 'done') 
                                AND date_order >= %s AND date_order <= %s
                            """, (start_str, end_str))
                            res = self.env.cr.fetchone()
                            s_count = res[0] or 0
                            s_total = res[1] or 0.0
                        
                        if 'pos.order' in self.env:
                            self.env.cr.execute("""
                                SELECT count(*), sum(amount_total) 
                                FROM pos_order 
                                WHERE state IN ('paid', 'done', 'invoiced') 
                                AND date_order >= %s AND date_order <= %s
                            """, (start_str, end_str))
                            res = self.env.cr.fetchone()
                            p_count = res[0] or 0
                            p_total = res[1] or 0.0
                        return s_total, s_count, p_total, p_count

                    s_rev_c, s_cnt_c, p_rev_c, p_cnt_c = get_revenue_breakdown(start_current, today)
                    s_rev_l, s_cnt_l, p_rev_l, p_cnt_l = get_revenue_breakdown(start_last_month, last_month_end)
                    
                    data += f"REVENUE_CURRENT_MONTH: Sales={s_rev_c}({s_cnt_c} orders), PoS={p_rev_c}({p_cnt_c} orders)\n"
                    data += f"REVENUE_LAST_MONTH: Sales={s_rev_l}({s_cnt_l} orders), PoS={p_rev_l}({p_cnt_l} orders)\n"
                    
                    if 'pos.order' in self.env and 'table_id' in self.env['pos.order']._fields:
                        rest_cnt = self.env['pos.order'].search_count([
                            ('state', 'in', ['paid', 'done', 'invoiced']),
                            ('date_order', '>=', fields.Datetime.to_string(start_current)),
                            ('table_id', '!=', False)
                        ])
                        data += f"RESTAURANT_ORDERS_THIS_MONTH: {rest_cnt}\n"
            except Exception as e:
                _logger.error("Error calculating revenue context: %s", str(e))

        # 3. Best Sellers (Last 30 days)
        if 'sale.order.line' in self.env or 'pos.order.line' in self.env:
            try:
                with self.env.cr.savepoint():
                    date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
                    
                    # Unified SQL for Sales and PoS best sellers
                    query = """
                        SELECT product_id, sum(qty) as total_qty, sum(price) as total_revenue
                        FROM (
                            SELECT l.product_id, l.product_uom_qty as qty, l.price_subtotal as price
                            FROM sale_order_line l
                            JOIN sale_order s ON l.order_id = s.id
                            WHERE s.state IN ('sale', 'done') AND s.date_order >= %s
                            UNION ALL
                            SELECT l.product_id, l.qty as qty, l.price_subtotal as price
                            FROM pos_order_line l
                            JOIN pos_order p ON l.order_id = p.id
                            WHERE p.state IN ('paid', 'done', 'invoiced') AND p.date_order >= %s
                        ) as combined_sales
                        GROUP BY product_id
                        ORDER BY total_revenue DESC
                        LIMIT 10
                    """
                    self.env.cr.execute(query, (date_30_days_ago, date_30_days_ago))
                    
                    for p_id, q, rev in self.env.cr.fetchall():
                        p = self.env['product.product'].browse(p_id)
                        margin = (p.list_price - p.standard_price) / p.list_price * 100 if p.list_price > 0 else 0
                        data += f"BEST_SELLER: Name='{p.name}', Sold={q}, Rev={rev}, Margin={margin:.1f}%, Stock={p.qty_available}\n"
            except Exception as e:
                _logger.error("Error calculating best sellers context: %s", str(e))

        # 4. Zero Stock Products
        try:
            zero_stock = self.env['product.product'].search([('qty_available', '=', 0), ('active', '=', True)], limit=10)
            if zero_stock:
                data += "ZERO_STOCK_PRODUCTS: " + ", ".join([p.name for p in zero_stock]) + "\n"
        except Exception as e:
            _logger.error("Error calculating zero stock context: %s", str(e))

        # 5. Top Customers (Last 30 days)
        if 'sale.order' in self.env or 'pos.order' in self.env:
            try:
                with self.env.cr.savepoint():
                    date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
                    
                    query = """
                        SELECT partner_id, sum(amount_total) as total_rev
                        FROM (
                            SELECT partner_id, amount_total
                            FROM sale_order
                            WHERE state IN ('sale', 'done') AND date_order >= %s
                            UNION ALL
                            SELECT partner_id, amount_total
                            FROM pos_order
                            WHERE state IN ('paid', 'done', 'invoiced') AND date_order >= %s
                        ) as combined_orders
                        WHERE partner_id IS NOT NULL
                        GROUP BY partner_id
                        ORDER BY total_rev DESC
                        LIMIT 5
                    """
                    self.env.cr.execute(query, (date_30_days_ago, date_30_days_ago))
                    for partner_id, rev in self.env.cr.fetchall():
                        partner = self.env['res.partner'].browse(partner_id)
                        data += f"TOP_CUSTOMER: Name='{partner.name}', TotalRevenue={rev}\n"
            except Exception as e:
                _logger.error("Error calculating top customers context: %s", str(e))
        
        # 6. CRM (Leads/Opportunities)
        if 'crm.lead' in self.env:
            try:
                leads_count = self.env['crm.lead'].search_count([('type', '=', 'lead'), ('active', '=', True)])
                ops = self.env['crm.lead'].search([('type', '=', 'opportunity'), ('probability', '<', 100), ('probability', '>', 0)])
                pipeline_val = sum(ops.mapped('expected_revenue'))
                data += f"CRM: Leads={leads_count}, ActiveOpportunities={len(ops)}, PipelineValue={pipeline_val}\n"
            except Exception as e:
                _logger.error("Error calculating CRM context: %s", str(e))

        # 7. Invoicing (Outstanding)
        if 'account.move' in self.env:
            try:
                invoices = self.env['account.move'].search([
                    ('move_type', '=', 'out_invoice'),
                    ('state', '=', 'posted'),
                    ('payment_state', 'not in', ['paid', 'reversed'])
                ])
                total_due = sum(invoices.mapped('amount_residual'))
                data += f"INVOICING: OutstandingInvoices={len(invoices)}, TotalDue={total_due}\n"
            except Exception as e:
                _logger.error("Error calculating Invoicing context: %s", str(e))

        # 8. Purchase (Pending POs)
        if 'purchase.order' in self.env:
            try:
                pos = self.env['purchase.order'].search([('state', 'in', ['purchase', 'done'])])
                data += f"PURCHASE: TotalActivePOs={len(pos)}, TotalPurchaseVal={sum(pos.mapped('amount_total'))}\n"
            except Exception as e:
                _logger.error("Error calculating Purchase context: %s", str(e))

        # 9. Manufacturing (MOs)
        if 'mrp.production' in self.env:
            try:
                mos_count = self.env['mrp.production'].search_count([('state', 'not in', ['done', 'cancel'])])
                data += f"MANUFACTURING: ActiveMOs={mos_count}\n"
            except Exception as e:
                _logger.error("Error calculating Manufacturing context: %s", str(e))

        # 10. Calendar (Today's Meetings)
        if 'calendar.event' in self.env:
            try:
                today_start = datetime.now().replace(hour=0, minute=0, second=0)
                today_end = today_start + timedelta(days=1)
                meetings = self.env['calendar.event'].search_count([
                    ('start', '>=', fields.Datetime.to_string(today_start)),
                    ('start', '<', fields.Datetime.to_string(today_end))
                ])
                data += f"CALENDAR: MeetingsToday={meetings}\n"
            except Exception as e:
                _logger.error("Error calculating Calendar context: %s", str(e))

        return data



    @api.model
    def cron_cleanup_old_messages(self):
        """Cleanup old messages."""
        ai_channel = self.env.ref('inventory_ai.channel_inventory_ai', raise_if_not_found=False)
        if not ai_channel: return True
        date_limit = datetime.now() - timedelta(days=30)
        self.env['mail.message'].search([
            ('res_id', '=', ai_channel.id),
            ('model', '=', 'discuss.channel'),
            ('create_date', '<', fields.Datetime.to_string(date_limit))
        ]).unlink()
        return True

    def _get_revenue_for_days(self, days):
        """Helper to get revenue for exactly X days."""
        date_limit = fields.Datetime.to_string(datetime.now() - timedelta(days=days))
        total = 0.0
        count = 0
        
        if 'sale.order' in self.env:
            s_orders = self.env['sale.order'].search([
                ('state', 'in', ['sale', 'done']),
                ('date_order', '>=', date_limit)
            ])
            total += sum(s_orders.mapped('amount_total'))
            count += len(s_orders)
            
        if 'pos.order' in self.env:
            p_orders = self.env['pos.order'].search([
                ('state', 'in', ['paid', 'done', 'invoiced']),
                ('date_order', '>=', date_limit)
            ])
            total += sum(p_orders.mapped('amount_total'))
            count += len(p_orders)
            
        return total, count

    def _get_ai_response_and_post(self, message):
        """AI Response logic."""

        try:
            prompt = message.body
            clean_prompt = re.sub('<[^<]+?>', '', prompt).strip()

            # The Ultimate Selective HTML Prompt
            system_prompt = (
                "You are the Gemini Strategic Analyst for Odoo. "
                "CRITICAL: Answer ONLY the specific question asked. Do not dump irrelevant data.\n"
                "You have access to: Sales, Point of Sale, Inventory, CRM, Invoicing, Purchase, Manufacturing, and Calendar.\n\n"
                "RULES:\n"
                "1. **OUTPUT**: Use PURE HTML only (<h3>, <ul>, <li>, <strong>, <br/>). NO Markdown.\n"
                "2. **SELECTIVITY**: Only show sections that answer the question.\n"
                "3. **VISUALS**: Use <h3> with emojis. Use <br/><br/> for spacing.\n"
                "4. **PRECISION**: Use the provided [RAW DATA] and [DYNAMIC DATA] for numbers.\n"
            )

            data_context = self._get_operational_context()
            
            # DYNAMIC DATA INJECTION (Last X Days Revenue)
            dynamic_context = "[DYNAMIC DATA]\n"
            day_match = re.search(r'last (\d+) days', clean_prompt.lower())
            if day_match:
                days = int(day_match.group(1))
                rev, count = self._get_revenue_for_days(days)
                dynamic_context += f"REVENUE_LAST_{days}_DAYS: Amount={rev}, Orders={count}\n"
            
            # Additional Product Search
            embedding_model = self.env['product.product.embedding']
            sim_products = embedding_model.search_similar_products(clean_prompt)
            search_context = "[SEARCH_RESULTS]\n"
            if sim_products:
                for p in sim_products[:5]:
                    search_context += f"PRODUCT: Name='{p.name}', Price={p.list_price}, Stock={p.qty_available}\n"
            
            full_prompt = (
                f"{system_prompt}\n\n"
                f"{data_context}\n\n"
                f"{dynamic_context}\n\n"
                f"{search_context}\n\n"
                f"User Question: {clean_prompt}"
            )
            response_text = self._call_gemini_api(full_prompt)

        except Exception as e:
            _logger.error("AI Error: %s", str(e))
            response_text = "I encountered an error. Please try again."

        bot_partner = self.env.ref('inventory_ai.partner_inventory_ai_bot', raise_if_not_found=False)
        self.with_context(mail_create_nosubscribe=True).message_post(
            body=Markup(response_text),
            author_id=bot_partner.id if bot_partner else self.env.ref('base.partner_root').id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )


    def _call_gemini_api(self, prompt):
        """
        Helper to call Google Gemini API with detailed error reporting.
        """
        api_key = self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_api_key')
        model_name = (self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_model_name') or 'gemini-2.0-flash').strip()
        
        # Clean model name
        model_name = model_name.replace('models/', '').strip('`"\' ')

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }

        try:
            if not api_key:
                _logger.error("Gemini API Key is missing in ir.config_parameter")
                return "AI configuration error: Missing API Key. Please configure it in Settings."

            _logger.info("Calling Gemini API: %s", url.split('?')[0])
            response = requests.post(url, headers=headers, data=json.dumps(data), timeout=30)
            
            if response.status_code == 429:
                _logger.warning("Gemini API Rate Limit: %s", response.text)
                return "The AI service is currently busy handling many requests. Please wait about a minute before trying again."
            
            if response.status_code != 200:
                _logger.error("Gemini API Error %s: %s", response.status_code, response.text)
                return f"I'm having trouble connecting to my central brain (Error {response.status_code}). Please try again in a few moments."
            
            result = response.json()
            candidates = result.get('candidates', [])
            if candidates:
                return candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            
            _logger.error("Gemini API returned no candidates: %s", result)
            return "I'm sorry, I couldn't generate a response. Could you please try again?"
        except requests.exceptions.Timeout:
            _logger.error("Gemini API Timeout")
            return "The AI service timed out. Please check your internet connection and try again."
        except Exception as e:
            _logger.error("AI Connection Error: %s", str(e), exc_info=True)
            return f"There seems to be a connection issue between Odoo and the AI service: {str(e)}. Please check your network or API key."

