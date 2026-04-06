content = open('app.js', 'rb').read().decode('utf-8')
# Add console.log right after the first line (// @ts-nocheck)
old_first = '// @ts-nocheck\r\nconst API = "/api";'
new_first = '// @ts-nocheck\r\nconsole.log("[app.js] EXECUTING - top of file");\r\nconst API = "/api";'
if old_first in content:
    content = content.replace(old_first, new_first, 1)
    open('app.js', 'wb').write(content.encode('utf-8'))
    print('SUCCESS: Added console.log at top of app.js')
else:
    print('ERROR: Pattern not found')
    print(repr(content[:120]))
