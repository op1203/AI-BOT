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

    # --- AI ANALYTICAL TOOLS (FOR FUNCTION CALLING) ---

    def _ai_tool_get_revenue_data(self, days=30):
        """Fetches revenue for a specific number of days."""
        from datetime import datetime, timedelta
        start_date = fields.Datetime.to_string(datetime.now() - timedelta(days=int(days)))
        orders = self.env['sale.order'].search([
            ('state', 'in', ['sale', 'done']),
            ('date_order', '>=', start_date)
        ])
        rev = sum(orders.mapped('amount_total'))
        return {"days": days, "revenue": rev, "order_count": len(orders)}

    def _ai_tool_get_top_products(self, days=30, limit=5):
        """Fetches top products for a specific timeframe."""
        from datetime import datetime, timedelta
        date_limit = fields.Datetime.to_string(datetime.now() - timedelta(days=int(days)))
        self.env.cr.execute("""
            SELECT product_id, sum(product_uom_qty) as total_qty, sum(price_subtotal) as total_revenue
            FROM sale_order_line
            WHERE state IN ('sale', 'done') AND create_date >= %s
            GROUP BY product_id
            ORDER BY total_qty DESC
            LIMIT %s
        """, (date_limit, int(limit)))
        results = []
        for p_id, q, rev in self.env.cr.fetchall():
            p = self.env['product.product'].browse(p_id)
            results.append({"name": p.name, "qty": q, "revenue": rev, "stock": p.qty_available})
        return results

    def _ai_tool_get_stock_status(self, product_query):
        """Finds detailed stock for a product."""
        embedding_model = self.env['product.product.embedding']
        products = embedding_model.search_similar_products(product_query)
        results = []
        for p in products[:3]:
            results.append({"name": p.name, "stock": p.qty_available, "price": p.list_price, "cost": p.standard_price})
        return results

    def _get_ai_response_and_post(self, message):
        """AI Response logic with Tool Calling."""
        import re
        import logging
        _logger = logging.getLogger(__name__)

        try:
            prompt = message.body
            clean_prompt = re.sub('<[^<]+?>', '', prompt).strip()

            # The Agentic Prompt
            system_prompt = (
                "You are the Gemini Strategic Analyst for Odoo. "
                "You have access to REAL-TIME TOOLS to query the database. "
                "If a user asks for revenue, stock, or customers for ANY timeframe (e.g. 7 days, 45 days), "
                "use the provided tools to get the data first.\n\n"
                "STYLE RULES:\n"
                "1. **SELECTIVE HTML**: Return only relevant sections in HTML (<h3>, <ul>, <li>, <strong>).\n"
                "2. **ACCURACY**: Use the exact numbers returned by the tools.\n"
                "3. **PROFESSIONAL**: No long paragraphs. Use clear headers and emojis."
            )

            # Define tools for Gemini
            tools = [{
                "function_declarations": [
                    {
                        "name": "get_revenue_data",
                        "description": "Get total revenue and order count for the last N days.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "days": {"type": "NUMBER", "description": "Number of days (e.g. 7, 30, 90)"}
                            },
                            "required": ["days"]
                        }
                    },
                    {
                        "name": "get_top_products",
                        "description": "Get the best-selling products for the last N days.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "days": {"type": "NUMBER", "description": "Number of days"},
                                "limit": {"type": "NUMBER", "description": "Max results (default 5)"}
                            },
                            "required": ["days"]
                        }
                    },
                    {
                        "name": "get_stock_status",
                        "description": "Get current stock levels for a specific product name or category.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "product_query": {"type": "STRING", "description": "Product name or search term"}
                            },
                            "required": ["product_query"]
                        }
                    }
                ]
            }]

            response_text = self._call_gemini_with_tools(clean_prompt, system_prompt, tools)

        except Exception as e:
            _logger.error("AI Error: %s", str(e))
            response_text = "I encountered an error while processing your request. Please try again."

        from markupsafe import Markup
        bot_partner = self.env.ref('inventory_ai.partner_inventory_ai_bot', raise_if_not_found=False)
        self.with_context(mail_create_nosubscribe=True).message_post(
            body=Markup(response_text),
            author_id=bot_partner.id if bot_partner else self.env.ref('base.partner_root').id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )

    def _call_gemini_with_tools(self, user_prompt, system_prompt, tools):
        """Advanced helper to handle tool calling loops."""
        import requests
        import json

        api_key = self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_api_key')
        model = (self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_model_name') or 'gemini-1.5-flash').strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        
        messages = [
            {"role": "user", "parts": [{"text": f"{system_prompt}\n\nUser Question: {user_prompt}"}]}
        ]

        # 1. First Call: See if tools are needed
        payload = {"contents": messages, "tools": tools}
        res = requests.post(url, json=payload, timeout=30).json()
        
        # Handle Tool Calls
        candidate = res.get('candidates', [{}])[0]
        content = candidate.get('content', {})
        parts = content.get('parts', [])
        
        if parts and 'functionCall' in parts[0]:
            tool_call = parts[0]['functionCall']
            fn_name = tool_call['name']
            args = tool_call.get('args', {})
            
            # Execute Tool
            result = {}
            if fn_name == "get_revenue_data":
                result = self._ai_tool_get_revenue_data(**args)
            elif fn_name == "get_top_products":
                result = self._ai_tool_get_top_products(**args)
            elif fn_name == "get_stock_status":
                result = self._ai_tool_get_stock_status(**args)
            
            # Second Call: Feed result back
            messages.append(content) # Response from model with functionCall
            messages.append({
                "role": "function",
                "parts": [{
                    "functionResponse": {
                        "name": fn_name,
                        "response": {"content": result}
                    }
                }]
            })
            
            final_res = requests.post(url, json={"contents": messages, "tools": tools}, timeout=30).json()
            final_candidate = final_res.get('candidates', [{}])[0]
            return final_candidate.get('content', {}).get('parts', [{}])[0].get('text', 'No answer generated.')
        
        return parts[0].get('text', 'No answer generated.')

    def _call_gemini_api(self, prompt):
        # Legacy fallback - usually not called now
        return ""
