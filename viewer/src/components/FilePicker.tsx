/** Open a `tree.json` from disk. Used by the loading screen and the sidebar. */

export function FilePicker({ onOpen }: { onOpen: (file: File) => void }) {
  return (
    <label className="picker">
      <input
        type="file"
        accept="application/json,.json"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onOpen(file);
        }}
      />
      <span>Open a tree.json…</span>
    </label>
  );
}
