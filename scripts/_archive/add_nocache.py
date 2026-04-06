content = open('app.py', 'rb').read().decode('utf-8')

# Add no-cache route for app.js after the index route
old = '@app.route("/")\r\ndef index():\r\n    return send_from_directory(".", "index.html")'
new = '@app.route("/")\ndef index():\n    return send_from_directory(".", "index.html")\n\n@app.route("/app.js")\ndef serve_appjs():\n    from flask import make_response\n    resp = make_response(send_from_directory(".", "app.js"))\n    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"\n    resp.headers["Pragma"] = "no-cache"\n    resp.headers["Expires"] = "0"\n    return resp'

# Try to find and fix it  
if '@app.route("/")\r\ndef index():' in content:
    fixed = content.replace('@app.route("/")\r\ndef index():\r\n    return send_from_directory(".", "index.html")', 
                            '@app.route("/")\ndef index():\n    return send_from_directory(".", "index.html")\n\n@app.route("/app.js")\ndef serve_appjs():\n    from flask import make_response\n    resp = make_response(send_from_directory(".", "app.js"))\n    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"\n    resp.headers["Pragma"] = "no-cache"\n    resp.headers["Expires"] = "0"\n    return resp')
    open('app.py', 'wb').write(fixed.encode('utf-8'))
    print('Added no-cache route for app.js')
else:
    print('Pattern not found - trying alternate search:')
    idx = content.find('@app.route("/")')
    print(repr(content[idx:idx+120]))
