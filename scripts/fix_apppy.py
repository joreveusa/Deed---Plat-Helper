import sys

# Read raw bytes
content = open('app.py', 'rb').read()
print(f'Original size: {len(content)} bytes')

# Count line ending types
crlf = content.count(b'\r\n')
crcrlf = content.count(b'\r\r\n')
print(f'Standard CRLF (\\r\\n) count: {crlf}')
print(f'Double CR CRLF (\\r\\r\\n) count: {crcrlf}')
print(f'First 100 bytes: {repr(content[:100])}')

# Fix: replace \r\r\n with \n (normalize to Unix LF)
fixed = content.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n')
print(f'Fixed size: {len(fixed)} bytes')

# Now insert the no-cache route for app.js
fixed_str = fixed.decode('utf-8')

old_route = '@app.route("/")\ndef index():\n    return send_from_directory(".", "index.html")'
new_route = '''@app.route("/")\ndef index():\n    from flask import make_response\n    resp = make_response(send_from_directory(".", "index.html"))\n    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"\n    return resp\n\n@app.route("/app.js")\ndef serve_appjs():\n    from flask import make_response\n    resp = make_response(send_from_directory(".", "app.js"))\n    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"\n    resp.headers["Pragma"] = "no-cache"\n    resp.headers["Expires"] = "0"\n    return resp'''

if old_route in fixed_str:
    fixed_str = fixed_str.replace(old_route, new_route, 1)
    open('app.py', 'wb').write(fixed_str.encode('utf-8'))
    print('SUCCESS: Line endings fixed + no-cache routes added')
else:
    print('ERROR: Pattern not found')
    idx = fixed_str.find('@app.route("/")')
    if idx >= 0:
        print(repr(fixed_str[idx:idx+150]))
    else:
        print('Route not found at all!')
