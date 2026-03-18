from odoo import models, fields, api, _

class ProductProductEmbedding(models.Model):
    _name = 'product.product.embedding'
    _description = 'Product Embeddings for RAG'

    product_id = fields.Many2one('product.product', string='Product', required=True, ondelete='cascade')
    # Storing as Text allows us to cast to ::vector in SQL if pgvector is present,
    # or parse as JSON in Python if it is missing.
    embedding = fields.Text(string='Embedding Vector', help='Stored as a JSON list of floats')
    
    @api.model
    def _create_vector_extension(self):
        """Enable pgvector extension in the database."""
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass

    def _is_vector_extension_installed(self):
        """Check if pgvector extension is installed in Postgres."""
        try:
            self.env.cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            return bool(self.env.cr.fetchone())
        except Exception:
            return False

    def _get_embedding(self, text):
        """Generate embedding using Gemini embedding model."""
        import requests
        import json
        
        api_key = self.env['ir.config_parameter'].sudo().get_param('inventory_ai.gemini_api_key')
        if not api_key:
            return None

        # Using v1beta for consistency with main API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "model": "models/text-embedding-004",
            "content": {
                "parts": [{"text": text}]
            }
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(data), timeout=15)
            response.raise_for_status()
            return response.json().get('embedding', {}).get('values', [])
        except Exception:
            return None

    @api.model
    def action_index_all_products(self):
        """Index all products by generating embeddings."""
        import json
        self._create_vector_extension()
        products = self.env['product.product'].search([('active', '=', True)])
        for product in products:
            text = f"Product: {product.name}. Description: {product.description_sale or ''}"
            embedding = self._get_embedding(text)
            if embedding:
                existing = self.search([('product_id', '=', product.id)])
                if existing:
                    existing.write({'embedding': json.dumps(embedding)})
                else:
                    self.create({
                        'product_id': product.id,
                        'embedding': json.dumps(embedding)
                    })
        return True

    def _search_similar_python(self, query_vector, limit=5):
        """Pure Python fallback for similarity search."""
        import json
        import math
        all_embeddings = self.search([])
        scored_products = []
        def dot_product(v1, v2): return sum(x * y for x, y in zip(v1, v2))
        def magnitude(v): return math.sqrt(sum(x * x for x in v))
        query_mag = magnitude(query_vector)
        for record in all_embeddings:
            try:
                vec = json.loads(record.embedding)
                if not vec: continue
                sim = dot_product(query_vector, vec) / (query_mag * magnitude(vec))
                scored_products.append((record.product_id.id, sim))
            except Exception: continue
        scored_products.sort(key=lambda x: x[1], reverse=True)
        return [p[0] for p in scored_products[:limit]]

    def search_similar_products(self, query_text, limit=5):
        """Search for similar products using pgvector (SQL) or Python fallback."""
        query_vector = self._get_embedding(query_text)
        if not query_vector:
            return self.env['product.product'].search([('name', 'ilike', query_text)], limit=limit)

        if self._is_vector_extension_installed():
            # USE PGVECTOR
            vector_str = f"[{','.join(map(str, query_vector))}]"
            query = """
                SELECT product_id 
                FROM product_product_embedding 
                ORDER BY embedding::vector <-> %s::vector 
                LIMIT %s
            """
            try:
                self.env.cr.execute(query, (vector_str, limit))
                product_ids = [row[0] for row in self.env.cr.fetchall()]
                return self.env['product.product'].browse(product_ids)
            except Exception:
                pass # Fallback to Python if SQL fails (e.g. casting issues)

        # FALLBACK TO PYTHON
        product_ids = self._search_similar_python(query_vector, limit=limit)
        return self.env['product.product'].browse(product_ids)
