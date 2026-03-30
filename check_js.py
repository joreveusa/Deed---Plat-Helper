import sys
with open('app.js', 'r', encoding='utf-8') as f:
    content = f.read()

in_template = False
template_depth = 0
brace_depth = 0
paren_depth = 0
line_num = 1
i = 0
errors = []

while i < len(content):
    ch = content[i]
    if ch == '\n':
        line_num += 1
        i += 1
        continue
    if ch in ('"', "'") and not in_template:
        quote = ch
        i += 1
        while i < len(content) and content[i] != quote:
            if content[i] == '\\': i += 1
            if i < len(content) and content[i] == '\n': line_num += 1
            i += 1
        i += 1
        continue
    if ch == '/' and i+1 < len(content) and content[i+1] == '/' and not in_template:
        while i < len(content) and content[i] != '\n':
            i += 1
        continue
    if ch == '/' and i+1 < len(content) and content[i+1] == '*' and not in_template:
        i += 2
        while i+1 < len(content) and not (content[i] == '*' and content[i+1] == '/'):
            if content[i] == '\n': line_num += 1
            i += 1
        i += 2
        continue
    if ch == '`':
        if in_template and template_depth == 0:
            in_template = False
        else:
            in_template = True
            template_depth = 0
        i += 1
        continue
    if in_template and ch == '$' and i+1 < len(content) and content[i+1] == '{':
        template_depth += 1
        i += 2
        continue
    if in_template and template_depth > 0 and ch == '}':
        template_depth -= 1
        i += 1
        continue
    if not in_template or template_depth > 0:
        if ch == '{': brace_depth += 1
        elif ch == '}': brace_depth -= 1
        elif ch == '(': paren_depth += 1
        elif ch == ')': paren_depth -= 1
        if brace_depth < 0:
            errors.append(f'Line {line_num}: Extra closing brace')
            brace_depth = 0
        if paren_depth < 0:
            errors.append(f'Line {line_num}: Extra closing paren')
            paren_depth = 0
    i += 1

print(f'in_template={in_template} template_depth={template_depth}')
print(f'braces={brace_depth} parens={paren_depth}')
if errors:
    for e in errors: print(e)
if in_template:
    print('ERROR: Unclosed template literal!')
elif brace_depth != 0:
    print(f'ERROR: Unbalanced braces: {brace_depth}')
elif paren_depth != 0:
    print(f'ERROR: Unbalanced parens: {paren_depth}')
else:
    print('SYNTAX: OK')
