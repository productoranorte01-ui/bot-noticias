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
        # Timeout de 15 segundos para evitar cuelgues
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

            # Protegido igual que "link": si falta el tag, no rompe el script
            original_title = item.find('title').text if item.find('title') is not None else ""
            original_desc = item.find('description').text if item.find('description') is not None else ""

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

            # Reescribir con modelo vigente de Groq (llama-3.3-70b-versatile fue dado de baja en agosto 2026)
            completion = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                reasoning_effort="high",  # Fuerza al modelo a "pensar" antes de responder en vez de copiar el texto de entrada
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sos un editor jefe de un portal de noticias digital. Tu misión es reescribir esta noticia de forma 100% ORIGINAL para evitar penalizaciones de Google por contenido duplicado.\n\n"
                            "REGLAS ESTRITAS Y OBLIGATORIAS:\n"
                            "1. TÍTULO: Está COMPLETAMENTE PROHIBIDO usar el título original o dejar frases idénticas entre comillas. Debes convertirlo en un titular periodístico de impacto, atractivo para Google Discover y sin comillas textuales.\n"
                            "2. COPETE / BAJADA: El primer párrafo del 'contenido' DEBE ser una bajada/resumen breve encerrada en la etiqueta HTML <strong>...</strong> que sintetice lo más importante de la noticia.\n"
                            "3. CUERPO: Parafraseá el resto del texto en 2 o 3 párrafos en formato HTML (<p>...</p>), usando sinónimos y oraciones propias pero MANTENIENDO la veracidad de los datos reales (nombres, cargos, fechas, lugares).\n"
                            "4. PROHIBIDO ABSOLUTO: no repitas ninguna oración completa del texto original, ni siquiera cambiando una o dos palabras. Cada oración debe estar redactada con estructura y vocabulario propios.\n\n"
                            "Respondé ÚNICAMENTE en JSON estricto: {\"titulo\":\"...\",\"contenido\":\"...\"}"
                        )
                    },
                    {
                        "role": "user",
                        "content": f"Título: {original_title}\nTexto: {original_desc}"
                    }
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            parsed = json.loads(completion.choices[0].message.content)

            # RED DE SEGURIDAD: si a pesar de todo la IA devolvió el título igual al original, no publicamos
            titulo_nuevo = (parsed.get("titulo") or "").strip()
            if titulo_nuevo.lower() == original_title.strip().lower():
                print(f"⚠️ La IA no reescribió el título (salió igual al original). Se salta esta nota para evitar contenido duplicado: {link}")
                continue

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

            # Publicar Entrada Directamente ("publish")
            post_data = {
                "title": parsed.get("titulo"),
                "content": parsed.get("contenido"),
                "status": "publish"
            }
            if media_id:
                post_data["featured_media"] = media_id

            wp_res = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", auth=(WP_USER, WP_APP_PASS), json=post_data, timeout=20)

            if wp_res.status_code in [200, 201]:
                print(f"✅ Entrada publicada con éxito (ID: {wp_res.json().get('id')})")
                publicadas.add(link)
                with open(ARCHIVO_HISTORIAL, "a", encoding="utf-8") as f:
                    f.write(link + "\n")
            else:
                print(f"❌ Error al conectar con WordPress: {wp_res.status_code}")

    except Exception as e:
        print(f"❌ Error procesando el feed {rss_url}: {e}")

print("\n🏁 Revisión automática finalizada.")
