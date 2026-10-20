// Keep callouts as portable Markdown blockquotes; add the existing site classes.
export function admonitions() {
  return (tree) => {
    function visit(node) {
      if (node.type === "blockquote") {
        const first = node.children?.[0]?.children?.[0];
        const match = first?.value?.match(
          /^\[!(NOTE|TIP|CAUTION)\](?: ([^\n]+))?\n?/,
        );
        if (match) {
          const kind = match[1].toLowerCase();
          first.value = first.value.slice(match[0].length);
          node.data = {
            hName: "aside",
            hProperties: { className: ["admonition", `admonition-${kind}`] },
          };
          node.children.unshift({
            type: "paragraph",
            data: { hProperties: { className: ["admonition-title"] } },
            children: [
              {
                type: "text",
                value: match[2] || kind[0].toUpperCase() + kind.slice(1),
              },
            ],
          });
        }
      }
      node.children?.forEach(visit);
    }
    visit(tree);
  };
}
