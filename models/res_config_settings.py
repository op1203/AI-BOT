from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='inventory_ai.gemini_api_key',
        help='API Key for Google Gemini'
    )
    gemini_model_name = fields.Char(
        string='Gemini Model Name',
        config_parameter='inventory_ai.gemini_model_name',
        default='gemini-2.0-flash',
        help='The model ID to use (e.g., gemini-2.0-flash, gemini-2.5-flash)'
    )
    def action_test_gemini_api(self):
        """Test the Gemini API connection and list available models."""
        import requests
        api_key = self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_api_key')
        if not api_key:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': 'API Key is missing!',
                    'type': 'danger',
                }
            }
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                models_data = response.json().get('models', [])
                model_names = [m['name'] for m in models_data if 'generateContent' in m.get('supportedGenerationMethods', [])]
                msg = f"Success! Available models for chat:\n" + "\n".join(model_names)
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'API Test Success',
                        'message': msg,
                        'type': 'success',
                        'sticky': True,
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'API Test Failed',
                        'message': f"Error {response.status_code}: {response.text}",
                        'type': 'danger',
                        'sticky': True,
                    }
                }
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Error',
                    'message': str(e),
                    'type': 'danger',
                }
            }

    def action_index_all_products_from_settings(self):
        """Index all products from the settings view."""
        return self.env['product.product.embedding'].action_index_all_products()
