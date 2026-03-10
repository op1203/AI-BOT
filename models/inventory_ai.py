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
            bot_author = self.env.ref('base.partner_root') # Using root as a placeholder bot
            if message.author_id != bot_author:
                self._get_ai_response_and_post(message)

    def _get_ai_response_and_post(self, message):
        """
        Calculates AI response using Gemini API and posts it back to the channel.
        """
        import requests
        import json
        import re

        # Get API Key from settings
        api_key = self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_api_key')
        if not api_key:
            self.message_post(body="Gemini API Key is not configured. Please set it in Settings > Inventory AI.")
            return

        prompt = message.body
        clean_prompt = re.sub('<[^<]+?>', '', prompt).strip()

        # System Prompt to define the bot's role
        system_prompt = (
            "You are an Inventory AI Assistant for Odoo. "
            "Your goal is to help users manage products, check stock, and analyze inventory. "
            "Keep your responses professional, concise, and helpful. "
            "If you don't know the answer based on Odoo data, say so."
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "contents": [{
                "parts": [
                    {"text": f"{system_prompt}\n\nUser Question: {clean_prompt}"}
                ]
            }]
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
            response.raise_for_status()
            result = response.json()
            
            # Extract text from Gemini response structure
            candidates = result.get('candidates', [])
            if candidates:
                response_text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            else:
                response_text = "I'm sorry, I couldn't generate a response at this time."

        except Exception as e:
            response_text = f"Error connecting to Gemini API: {str(e)}"

        # Post the message back to the channel
        self.with_context(mail_create_nosubscribe=True).message_post(
            body=response_text,
            author_id=self.env.ref('base.partner_root').id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )
