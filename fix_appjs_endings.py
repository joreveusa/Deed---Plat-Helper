# Fix app.js line endings: \r\r\n -> \n
content = open('app.js', 'rb').read()
print(f"Original size: {len(content)} bytes")
print(f"First 100 bytes: {repr(content[:100])}")

# Fix the double carriage return issue: replace \r\r\n with \n
fixed = content.replace(b'\r\r\n', b'\n')
print(f"Fixed size: {len(fixed)} bytes")
print(f"First 100 bytes after fix: {repr(fixed[:100])}")

open('app.js', 'wb').write(fixed)
print("DONE - app.js line endings fixed")
