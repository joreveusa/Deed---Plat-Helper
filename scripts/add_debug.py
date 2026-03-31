import re, time

ts = int(time.time())
content = open('index.html', 'r', encoding='utf-8').read()

# Add an inline debug script before app.js and update version
debug_script = f'''  <script>
    // Debug: verify JS execution
    console.log('[DEBUG] Inline script running at', new Date().toISOString());
    window._jsDebug = true;
    document.addEventListener('DOMContentLoaded', function() {{
      console.log('[DEBUG] DOMContentLoaded fired');
      fetch('/api/config')
        .then(r => r.json())
        .then(d => console.log('[DEBUG] /api/config response:', JSON.stringify(d)))
        .catch(e => console.error('[DEBUG] /api/config error:', e));
    }});
  </script>
  <script src="app.js?v={ts}"></script>'''

# Replace existing script tag
new_content = re.sub(r'  <script src="app\.js\?v=\d+"></script>', debug_script, content)
if new_content != content:
    open('index.html', 'w', encoding='utf-8').write(new_content)
    print(f'Debug script added, version: {ts}')
else:
    print('Pattern not found!')
    idx = content.find('<script src="app.js')
    print(repr(content[max(0,idx-5):idx+60]))
