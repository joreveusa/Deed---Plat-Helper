content = open('app.py', 'rb').read().decode('utf-8')
old = 'app.run(host="127.0.0.1", port=5000, debug=False)'
new = 'app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)'
if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'wb').write(content.encode('utf-8'))
    print('PATCHED: threaded=True added')
else:
    print('ERROR: target string not found')
