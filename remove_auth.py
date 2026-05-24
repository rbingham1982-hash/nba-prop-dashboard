"""Remove subscriber gate from the 4 locked tabs."""

with open('nba_prop_dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

def remove_gate_and_deindent(lines, gate_line_text, end_markers):
    """
    Find the line matching gate_line_text, remove it, then de-indent (strip 4 spaces)
    all subsequent lines until a line starting with one of end_markers is found.
    Returns modified lines list and the index where the block ended.
    """
    for i, line in enumerate(lines):
        if line.rstrip('\n') == gate_line_text:
            # Remove the gate line
            lines.pop(i)
            # De-indent everything from i onward until end marker
            j = i
            while j < len(lines):
                stripped = lines[j].rstrip()
                if any(stripped.startswith(m) for m in end_markers):
                    break
                if lines[j].startswith('    '):  # has at least 4 spaces
                    lines[j] = lines[j][4:]      # strip 4 spaces
                j += 1
            return lines, i
    print(f"  WARNING: gate line not found: {gate_line_text!r}")
    return lines, -1

END = ['    # ──', '    with tab_', '# ══']

lines, _ = remove_gate_and_deindent(lines, '        if _subscriber_gate("nba_parlays"):', END)
print("Removed nba_parlays gate")

lines, _ = remove_gate_and_deindent(lines, '        if _subscriber_gate("nba_accuracy"):', END)
print("Removed nba_accuracy gate")

lines, _ = remove_gate_and_deindent(lines, '        if _subscriber_gate("mlb_parlays"):', END)
print("Removed mlb_parlays gate")

lines, _ = remove_gate_and_deindent(lines, '        if _subscriber_gate("mlb_accuracy"):', END)
print("Removed mlb_accuracy gate")

with open('nba_prop_dashboard.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done.")
