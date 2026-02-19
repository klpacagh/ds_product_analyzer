uv run uvicorn ds_product_analyzer.api.app:app --reload

There's no built-in reset command. The app uses SQLite stored at ./data.db (relative to where you run the app). Here are your options:                                                                                            
                                                                                                                                                                                                                                
Quickest — delete and recreate:                                                                                                                                                                                                   
rm data.db                                                                                                                                                                                                                        


uv run alembic upgrade head                                                                                                                                                                                                       
                                                                                                                                                                                                                                
Or via Alembic:
alembic downgrade base   # drops all tables                                                                                                                                                                                       
alembic upgrade head     # recreates them empty                                                                                                                                                                                   
                                                                                                                                                                                                                                
Or selective clear (keep schema intact):
sqlite3 data.db "DELETE FROM trend_scores; DELETE FROM raw_signals; DELETE FROM price_history; DELETE FROM product_aliases; DELETE FROM products; DELETE FROM categories;"



uv run python -c "                                                                                                                                                                                                                                           
import sqlite3, json                                                                                                                                                                                                                                         
                                                                            
conn = sqlite3.connect('data.db')                                                                                                                                                                                                                            
conn.execute('DELETE FROM categories')                                                                                                                                                                                                                       

categories = [
    ('Home & Kitchen', ['standing desk', 'led strip lights', 'air purifier', 'portable blender', 'sunrise alarm clock']),
    ('Tech & Gadgets', ['mini projector', 'wireless earbuds', 'portable monitor', 'mechanical keyboard', 'smart plug']),
    ('Health & Wellness', ['massage gun', 'posture corrector', 'blue light glasses', 'electric toothbrush', 'foam roller']),
    ('Outdoors & Travel', ['portable charger', 'packing cubes', 'insulated water bottle', 'camping lantern', 'travel pillow']),
]

conn.executemany(
    'INSERT INTO categories (name, seed_keywords, active) VALUES (?, ?, 1)',
    [(name, json.dumps(kws)) for name, kws in categories],
)
conn.commit()

for row in conn.execute('SELECT name, seed_keywords FROM categories'):
    print(f'{row[0]}: {row[1]}')
conn.close()
"
