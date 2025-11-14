import http.server
import ssl
import os

os.chdir('/media/prth/GameSpace/Sidequest')
handler = http.server.SimpleHTTPRequestHandler
httpd = http.server.HTTPServer(('0.0.0.0', 8000), handler)

context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('cert.pem', 'key.pem')
httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

print("🚀 HTTPS Server running on https://YOUR_PC_IP:8000")
print("   (Ignore SSL warning on first visit)")
httpd.serve_forever()