from odoo import models, fields, api, _

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
                        f"background: #e9ecef; border-radius: 8px; border: 1px solid #ced4da;'>"
                        f"<strong style='color: #495057;'>🚀 NEW SESSION STARTED — {today_str}</strong><br/>"
                        f"<small style='color: #6c757d;'>Historical context for the last 30 days is preserved.</small></div>"
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
        from datetime import datetime, timedelta
        import calendar
        
        data = "[RAW DATA FOR ANALYSIS]\n"
        
        # 1. Stats
        product_count = self.env['product.product'].search_count([('active', '=', True)])
        all_products = self.env['product.product'].search([('active', '=', True)])
        total_qty_on_hand = sum(all_products.mapped('qty_available'))
        data += f"STATS: ActiveProducts={product_count}, TotalStock={total_qty_on_hand}\n"

        # 2. Revenue
        if 'sale.order' in self.env:
            today = datetime.now()
            start_current = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end = start_current - timedelta(days=1)
            start_last_month = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            def get_revenue(start, end):
                orders = self.env['sale.order'].search([
                    ('state', 'in', ['sale', 'done']),
                    ('date_order', '>=', fields.Datetime.to_string(start)),
                    ('date_order', '<=', fields.Datetime.to_string(end))
                ])
                return sum(orders.mapped('amount_total')), len(orders)

            rev_current, count_current = get_revenue(start_current, today)
            rev_last, count_last = get_revenue(start_last_month, last_month_end)
            data += f"REVENUE: LastMonth={rev_last}({count_last} orders), CurrentMonth={rev_current}({count_current} orders)\n"

        # 3. Best Sellers & Zero Stock
        if 'sale.order.line' in self.env:
            date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
            
            # Best Sellers
            self.env.cr.execute("""
                SELECT product_id, sum(product_uom_qty) as total_qty, sum(price_subtotal) as total_revenue
                FROM sale_order_line
                WHERE state IN ('sale', 'done') AND create_date >= %s
                GROUP BY product_id
                ORDER BY total_qty DESC
                LIMIT 5
            """, (date_30_days_ago,))
            for p_id, q, rev in self.env.cr.fetchall():
                p = self.env['product.product'].browse(p_id)
                margin = (p.list_price - p.standard_price) / p.list_price * 100 if p.list_price > 0 else 0
                data += f"BEST_SELLER: Name='{p.name}', Sold={q}, Rev={rev}, Margin={margin:.1f}%, Stock={p.qty_available}\n"

            # Zero Stock Products (Active only)
            zero_stock = self.env['product.product'].search([('qty_available', '=', 0), ('active', '=', True)], limit=10)
            if zero_stock:
                data += "ZERO_STOCK_PRODUCTS: " + ", ".join([p.name for p in zero_stock]) + "\n"

        # 4. Top Customers (Last 30 days)
        if 'sale.order' in self.env:
            date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
            self.env.cr.execute("""
                SELECT partner_id, sum(amount_total) as total_rev
                FROM sale_order
                WHERE state IN ('sale', 'done') AND date_order >= %s
                GROUP BY partner_id
                ORDER BY total_rev DESC
                LIMIT 5
            """, (date_30_days_ago,))
            for partner_id, rev in self.env.cr.fetchall():
                partner = self.env['res.partner'].browse(partner_id)
                data += f"TOP_CUSTOMER: Name='{partner.name}', TotalRevenue={rev}\n"

        return data

    def _get_conversation_history(self, limit_days=30):
        """Raw history for AI."""
        from datetime import datetime, timedelta
        date_limit = datetime.now() - timedelta(days=limit_days)
        messages = self.env['mail.message'].search([
            ('res_id', '=', self.id),
            ('model', '=', 'discuss.channel'),
            ('message_type', '=', 'comment'),
            ('create_date', '>=', fields.Datetime.to_string(date_limit))
        ], order='create_date asc', limit=30)
        return "\n".join([f"{m.author_id.name}: {m.body}" for m in messages])

    @api.model
    def cron_cleanup_old_messages(self):
        """Cleanup old messages."""
        from datetime import datetime, timedelta
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
        from datetime import datetime, timedelta
        date_limit = fields.Datetime.to_string(datetime.now() - timedelta(days=days))
        orders = self.env['sale.order'].search([
            ('state', 'in', ['sale', 'done']),
            ('date_order', '>=', date_limit)
        ])
        return sum(orders.mapped('amount_total')), len(orders)

    def _get_ai_response_and_post(self, message):
        """AI Response logic."""
        import re
        import logging
        _logger = logging.getLogger(__name__)

        try:
            prompt = message.body
            clean_prompt = re.sub('<[^<]+?>', '', prompt).strip()

            # The Ultimate Selective HTML Prompt
            system_prompt = (
                "You are the Gemini Strategic Analyst for Odoo. "
                "CRITICAL: Answer ONLY the specific question asked. Do not dump irrelevant data.\n\n"
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
            
            history_context = "[HISTORY]\n" + self._get_conversation_history()
            
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
                f"{history_context}\n\n"
                f"{search_context}\n\n"
                f"User Question: {clean_prompt}"
            )
            response_text = self._call_gemini_api(full_prompt)

        except Exception as e:
            _logger.error("AI Error: %s", str(e))
            response_text = "I encountered an error. Please try again."

        from markupsafe import Markup
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
        import requests
        import json

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
            response = requests.post(url, headers=headers, data=json.dumps(data), timeout=15)
            if response.status_code == 429:
                return "The AI service is currently busy handling many requests. Please wait about a minute before trying again."
            
            if response.status_code != 200:
                return "I'm having a little trouble connecting to my central brain right now. Please try again in a few moments."
            
            result = response.json()
            candidates = result.get('candidates', [])
            if candidates:
                return candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            return "I'm sorry, I couldn't summarize that right now. Could you please try again?"
        except Exception as e:
            return "There seems to be a connection issue between Odoo and the AI service. Please check your network or API key."
