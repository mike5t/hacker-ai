import markdown
import os
import base64
import re

with open('Clawd_Whitepaper.md', 'r', encoding='utf-8') as f:
    text = f.read()

# Base64 encode the architecture svg to inject it inline, bypassing edge file:// security
if os.path.exists('architecture.svg'):
    with open('architecture.svg', 'rb') as svg:
        b64 = base64.b64encode(svg.read()).decode('utf-8')
        text = text.replace('![Architecture Diagram](architecture.svg)', f'<img src="data:image/svg+xml;base64,{b64}" alt="Architecture Diagram" style="width: 100%; max-width: 800px; display: block; margin: 0 auto;"/>')

# adding a simple style to the HTML for better PDF rendering
html_content = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        line-height: 1.6;
        margin: 40px;
        color: #333;
    }}
    h1, h2, h3 {{
        color: #2c3e50;
    }}
    code {{
        background-color: #f8f9fa;
        padding: 2px 4px;
        border-radius: 4px;
        font-family: Consolas, monospace;
    }}
    pre {{
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 5px;
        overflow-x: auto;
    }}
    pre code {{
        background-color: transparent;
        padding: 0;
    }}
</style>
</head>
<body>
{markdown.markdown(text, extensions=['fenced_code', 'tables'])}
</body>
</html>
"""

with open('Clawd_Whitepaper.html', 'w', encoding='utf-8') as f:
    f.write(html_content)
