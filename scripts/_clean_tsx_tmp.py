import re

filepath = 'tools/experiment_monitor/frontend/index.tsx'
with open(filepath, 'r') as f:
    content = f.read()

original_len = len(content)

# Remove ScriptActionFormState type definition (multiline block)
content = re.sub(r'\ntype ScriptActionFormState = \{[^\}]+\};\n', '\n', content)

# Remove emptyScriptAction constant (multiline block)
content = re.sub(r'\nconst emptyScriptAction: ScriptActionFormState = \{[^\}]+\};\n', '\n', content)

# Remove scriptActionForm state line
content = re.sub(r'\n  const \[scriptActionForm, setScriptActionForm\] = useState<ScriptActionFormState>\(emptyScriptAction\);\n', '\n', content)

# Remove ScriptActionForm function (from "function ScriptActionForm" to matching closing "}\n\n")
# Find the block boundaries
start_marker = '\nfunction ScriptActionForm(props: {'
end_marker = '\nfunction EmailConfigForm(props: {'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + '\n' + content[end_idx:]
    print(f'Removed ScriptActionForm block ({end_idx - start_idx} chars)')
else:
    print(f'ScriptActionForm not found: start={start_idx}, end={end_idx}')

with open(filepath, 'w') as f:
    f.write(content)

print(f'Done. Original: {original_len}, New: {len(content)} chars')
