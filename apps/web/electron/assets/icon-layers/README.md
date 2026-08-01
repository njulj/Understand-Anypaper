# Understand Anypaper icon layers

Every SVG uses the same transparent 1024 × 1024 canvas. Import them into Photoshop as separate layers without resizing, then stack them in this order:

1. `page.svg`
2. `fold.svg`
3. `argument-lines.svg`
4. `argument-node-rings.svg` (transparent centers; separates lines from nodes)
5. `argument-nodes.svg`

`argument-lines.svg` intentionally uses open paths rather than a closed triangle, so changing its fill in Photoshop will not fill the hollow center.

The existing `../understand-anypaper-icon.svg` is the merged reference version.
