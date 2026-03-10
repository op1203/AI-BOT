from odoo import http
from odoo.http import request

class InventoryAIController(http.Controller):

    @http.route('/inventory_ai/chat', type='json', auth='user')
    def chat(self, prompt, **kwargs):
        # Placeholder for AI logic. You can integrate Gemini/OpenAI here.
        response = self._get_ai_response(prompt)
        return response

    def _get_ai_response(self, prompt):
        prompt = prompt.lower()
        if "hello" in prompt or "hi" in prompt:
            return "Hi there! How can I assist you with your inventory today?"
        elif "stock" in prompt or "inventory" in prompt:
            return "I can help you check stock levels or move products. What specifically are you looking for?"
        elif "product" in prompt:
            return "Which product would you like to know about?"
        else:
            return f"I received your message: '{prompt}'. I'm still learning, but I'll do my best to help!"
