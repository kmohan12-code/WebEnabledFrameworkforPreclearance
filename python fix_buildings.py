import xml.etree.ElementTree as ET
import os
import re

# 1. SETUP
filename = "buildings.poly.xml"
print(f"Working in folder: {os.getcwd()}")

if not os.path.exists(filename):
    print(" ERROR: I cannot find 'buildings.poly.xml'.")
    print("   Please FILE -> OPEN FOLDER and select your 'project' folder.")
    exit()

print(f" Found {filename}. Scanning now...")

# 2. READ FILE CONTENT AS TEXT (To avoid XML parsing errors for now)
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

# 3. FIX SPECIFIC KNOWN ERRORS (The ones you saw)
if "154 Breakfast Club" in content:
    print("   -> Found '154 Breakfast Club'! Fixing it...")

# 4. NUCLEAR FIX: Regex replace inside id="..."
# This pattern finds id="something" and replaces bad characters inside it
def clean_id(match):
    full_tag = match.group(0) # e.g. id="154 Breakfast Club"
    
    # Extract the name inside quotes
    start_quote = full_tag.find('"') + 1
    end_quote = full_tag.rfind('"')
    name = full_tag[start_quote:end_quote]
    
    # Replace anything that isn't a Letter, Number, or Underscore
    clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
    
    return f'id="{clean_name}"'

# Regex to find id attributes
# This matches: id=" followed by any text, followed by "
pattern = re.compile(r'id="[^"]+"')
new_content = pattern.sub(clean_id, content)

# 5. SAVE
with open(filename, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("-" * 30)
print(" SUCCESS! File saved.")
print("   Please check the 'Date Modified' of buildings.poly.xml in your folder.")
print("-" * 30)