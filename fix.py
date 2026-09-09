import os
import re

for root, _, files in os.walk("app/routes"):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            with open(path, "r") as f:
                content = f.read()
            
            # Simple approach: 
            content = re.sub(
                r'templates\.TemplateResponse\(\s*(["\'].+?["\'])\s*,\s*(\{.*?\})\s*\)',
                r'templates.TemplateResponse(request, \1, \2)',
                content,
                flags=re.DOTALL
            )
            
            with open(path, "w") as f:
                f.write(content)
