import json
import os
import urllib.request
import xml.etree.ElementTree as ET
import requests
from groq import Groq

# Cargar secretos guardados en GitHub Actions
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WP_URL = os.environ.get("WP_URL")
WP_USER = os.environ.get("WP_USER")
WP_APP_PASS = os.environ.get("WP_APP_PASS")

ARCHIVO_HISTORIAL = "historial.txt"

# LISTA DE FEEDS RSS
FEEDS_RSS = [
    "https://dib.com.ar/rss/pages/ultimas-noticias.xml",
]

# 1. Cargar la memoria de notas ya publicadas
if os.path.exists(ARCHIVO_HISTORIAL):
    with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
        publicadas = set(f.read().splitlines())
else:
    publicadas = set()

client = Groq(api_key=GROQ_API_KEY)

# 2. Recorrer los feeds
for rss_url in FEEDS_RSS:
    print(f"\n📡 Revisando feed: {rss_url}")
    try:
        # Agregamos timeout=15 para evitar bloqueos
        req = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
        xml_data = urllib.request.urlopen(req, timeout=15).read()
        root = ET.fromstring(xml_data)
        
        items = root.findall('.//item')[:3]
        
        for item in items:
            link = item.find('link').text if item.find('link') is not None else ""
            
            # CONTROL DE DUPLICADOS
            if link in publicadas:
                print(f"⏩ Ya publicada, salteando: {link}")
                continue
                
            original_title = item.find('title').text or ""
            original_desc = item.find('description').text or ""
            
            # Buscar Imagen Destacada
            image_url = None
            enclosure = item.find('enclosure')
            if enclosure is not None:
                image_url = enclosure.get('url')
            else:
                media_c = item.find('.//{http://search.yahoo.com/mrss/}content')
                if media_c is not None:
                    image_url = media_c.get('url')

            print(f"🤖 Procesando: {original_title[:40]}...")
            
            # Reescribir con Llama 3.3
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": "Sos un periodista profesional. Reescribí la noticia recibida cambiando el título por uno más atractivo para Google Discover y adaptando la redacción del cuerpo, MANTENIENDO ESTRICTAMENTE LA VERACIDAD DE LOS HECHOS. Respondé ÚNICAMENTE en formato JSON estricto: {\"titulo\":\"...\",\"contenido\":\"...\"}"
                    },
                    {
                        "role": "user",
                        "content": f"Título: {original_title}\nTexto: {original_desc}"
                    }
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            parsed = json.loads(completion.choices[0].message.content)
            
            # Subir Imagen a WordPress
            media_id = None
            if image_url:
                try:
                    img_resp = requests.get(image_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
                    if img_resp.status_code == 200 and len(img_resp.content) > 0:
                        filename = image_url.split("/")[-1].split("?")[0]
                        if not filename.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            filename = "imagen_destacada.jpg"
                        files = {'file': (filename, img_resp.content, 'image/jpeg')}
                        media_res = requests.post(f"{WP_URL}/wp-json/wp/v2/media", auth=(WP_USER, WP_APP_PASS), files=files, timeout=20)
                        if media_res.status_code in [200, 201]:
                            media_id = media_res.json().get('id')
                except Exception as e:
                    print(f"⚠️ Error al subir imagen: {e}")

            # Publicar Entrada Directamente (publish)
            post_data = {
                "title": parsed.get("titulo"),
                "content": parsed.get("contenido"),
                "status": "publish"
            }
            if media_id:
                post_data["featured_media"] = media_id
            
            wp_res = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", auth=(WP_USER, WP_APP_PASS), json=post_data, timeout=20)
            
            if wp_res.status_code in [200, 201]:
                print(f"✅ Entrada publicada (ID: {wp_res.json().get('id')})")
                publicadas.add(link)
                with open(ARCHIVO_HISTORIAL, "a", encoding="utf-8") as f:
                    f.write(link + "\n")
            else:
                print(f"❌ Error al conectar con WordPress: {wp_res.status_code}")
                
    except Exception as e:
        print(f"❌ Error procesando el feed {rss_url}: {e}")

print("\n🏁 Revisión automática finalizada.")
