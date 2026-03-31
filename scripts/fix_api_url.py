content = open('app.js', 'rb').read().decode('utf-8')
old = 'const API = "http://127.0.0.1:5000/api";'
new = 'const API = "/api";  // Use relative URL to avoid CORS issues'
if old in content:
    content = content.replace(old, new, 1)
    open('app.js', 'wb').write(content.encode('utf-8'))
    print('SUCCESS: API constant changed to relative URL')
else:
    print('ERROR: Pattern not found')
    idx = content.find('const API')
    print(repr(content[idx:idx+60]))
