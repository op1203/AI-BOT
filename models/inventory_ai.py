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
                self._get_ai_response_and_post(message)

    def _get_operational_context(self):
        """Fetches live context about deliveries, sales, and global Odoo statistics including Sales Analysis."""
        from datetime import datetime, timedelta
        # Use code blocks to prevent Odoo's frontend from parsing numbers as message references
        context = "```text\n--- GLOBAL ODOO STATISTICS ---\n"
        
        # 1. Global Counts & Totals
        product_count = self.env['product.product'].search_count([('active', '=', True)])
        
        # Calculate Total On-Hand for ALL products (Aggregated)
        # Using read_group for efficiency if possible
        all_products = self.env['product.product'].search([('active', '=', True)])
        total_qty_on_hand = sum(all_products.mapped('qty_available'))
        
        category_count = self.env['product.category'].search_count([])
        partner_count = self.env['res.partner'].search_count([('is_company', '=', True)])
        
        context += f"Total Active Products: {product_count}\n"
        context += f"Total Quantity On Hand (All Products): {total_qty_on_hand}\n"
        context += f"Product Categories: {category_count}\n"
        context += f"Total Business Partners: {partner_count}\n"

        # 2. Sales Analysis (Last 30 Days)
        if 'sale.order.line' in self.env:
            date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
            
            # Best Sellers (Top 5)
            self.env.cr.execute("""
                SELECT product_id, sum(product_uom_qty) as total_qty
                FROM sale_order_line
                WHERE state IN ('sale', 'done') AND create_date >= %s
                GROUP BY product_id
                ORDER BY total_qty DESC
                LIMIT 5
            """, (date_30_days_ago,))
            best_sellers_data = self.env.cr.fetchall()
            
            if best_sellers_data:
                context += "\nBest Selling Products (Last 30 Days):\n"
                for p_id, q in best_sellers_data:
                    p = self.env['product.product'].browse(p_id)
                    context += f"- {p.name}: {q} units sold | Currently {p.qty_available} on hand\n"

            # Least Sellers (Products with stock but low/zero sales)
            self.env.cr.execute("""
                SELECT product_id, sum(product_uom_qty) as total_qty
                FROM sale_order_line
                WHERE state IN ('sale', 'done') AND create_date >= %s
                GROUP BY product_id
                ORDER BY total_qty ASC
                LIMIT 5
            """, (date_30_days_ago,))
            least_sellers_data = self.env.cr.fetchall()

            if least_sellers_data:
                context += "\nLeast Selling Products (Last 30 Days):\n"
                for p_id, q in least_sellers_data:
                    p = self.env['product.product'].browse(p_id)
                    context += f"- {p.name}: {q} units sold | Currently {p.qty_available} on hand\n"

        context += "\n--- LIVE OPERATIONAL DATA ---\n"
        today = fields.Date.context_today(self)
        
        # 3. Today's Deliveries (Picking)
        if 'stock.picking' in self.env:
            from dateutil.relativedelta import relativedelta
            pickings = self.env['stock.picking'].search([
                ('scheduled_date', '>=', today),
                ('scheduled_date', '<', fields.Date.to_string(fields.Date.from_string(today) + relativedelta(days=1))),
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'not in', ['done', 'cancel'])
            ], limit=10)
            
            
        context += "```\n"
        return context

    def _get_ai_response_and_post(self, message):
        """
        Calculates AI response using Gemini API and posts it back to the channel.
        """
        import re
        import logging
        _logger = logging.getLogger(__name__)

        try:
            prompt = message.body
            clean_prompt = re.sub('<[^<]+?>', '', prompt).strip()

            # "Business Consultant" System Prompt
            system_prompt = (
                "You are the Odoo Business Consultant. "
                "Your goal is to provide insightful, human-like advice to the user. "
                "Don't just list data; analyze it. Specifically:\n"
                "1. If a product is a 'Best Seller' but has 'Low Stock' (less than average monthly sales), warn the user to restock.\n"
                "2. If a product has 'High Stock' but 'Low Sales', suggest a promotion or reducing future orders.\n"
                "3. Speak naturally, as a professional colleague. Avoid technical jargon or saying 'the provided context'.\n"
                "4. Be authoritative and confident in your recommendations.\n\n"
                "NEVER include technical data like 'RAG Source' or 'SQL Query' in your output."
            )

            # Step 1: Operational & BI Context
            context = self._get_operational_context() + "\n"
            
            # Step 2: Product Context (Vector Search)
            embedding_model = self.env['product.product.embedding']
            similar_products = embedding_model.search_similar_products(clean_prompt)
            if similar_products:
                context += "```text\n--- SPECIFIC PRODUCT DETAILS ---\n"
                for product in similar_products:
                    qty = product.with_context(location=False).qty_available
                    context += f"- {product.name}: {qty} {product.uom_id.name} in stock.\n"
                context += "```\n"
            
            full_prompt = f"{system_prompt}\n\n{context}\n\nUser Question: {clean_prompt}"
            
            response_text = self._call_gemini_api(full_prompt)

        except Exception as e:
            _logger.error("Inventory AI Error: %s", str(e))
            response_text = "I'm sorry, I encountered a slight internal hiccup while analyzing your data. Please try again or re-index your products."

        # Post the message back to the channel
        bot_partner = self.env.ref('inventory_ai.partner_inventory_ai_bot', raise_if_not_found=False)
        self.with_context(mail_create_nosubscribe=True).message_post(
            body=response_text,
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
