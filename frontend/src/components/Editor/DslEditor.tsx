import Editor, { type OnMount } from "@monaco-editor/react";
import type * as Monaco from "monaco-editor";
import { useEffect, useRef } from "react";
import type { CompileError } from "../../types";
import { LANG_ID, registerMonasql, applyMarkers } from "../../lib/monasqlLang";

export interface EditorApi {
  insertText: (text: string) => void;
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  errors: CompileError[];
  onReady?: (api: EditorApi) => void;
  onRun?: () => void;
}

export function DslEditor({ value, onChange, errors, onReady, onRun }: Props) {
  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof Monaco | null>(null);

  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    registerMonasql(monaco);
    monaco.editor.setTheme("monasql-dark");

    // Ctrl/Cmd+Enter → run
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRun?.());

    // drag a table/column from the schema panel → insert at drop point
    const dom = editor.getDomNode();
    if (dom) {
      dom.addEventListener("dragover", (e) => e.preventDefault());
      dom.addEventListener("drop", (e) => {
        e.preventDefault();
        const text = e.dataTransfer?.getData("text/plain");
        if (!text) return;
        const target = editor.getTargetAtClientPoint(e.clientX, e.clientY);
        const pos = target?.position ?? editor.getPosition();
        if (pos) {
          editor.executeEdits("drop", [
            { range: new monaco.Range(pos.lineNumber, pos.column, pos.lineNumber, pos.column), text },
          ]);
          editor.focus();
        }
      });
    }

    onReady?.({
      insertText: (text: string) => {
        const pos = editor.getPosition();
        if (!pos) return;
        editor.executeEdits("insert", [
          { range: new monaco.Range(pos.lineNumber, pos.column, pos.lineNumber, pos.column), text },
        ]);
        editor.focus();
      },
    });
  };

  // push compiler errors as inline markers
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    const model = editor?.getModel();
    if (editor && monaco && model) applyMarkers(monaco, model, errors);
  }, [errors]);

  return (
    <Editor
      language={LANG_ID}
      theme="monasql-dark"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={handleMount}
      options={{
        fontSize: 13,
        fontFamily: "JetBrains Mono, Menlo, monospace",
        minimap: { enabled: true },
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        renderWhitespace: "none",
        padding: { top: 10 },
        // Don't let Enter accept a preselected suggestion — only Tab does.
        // (Enter always inserts a newline, so editing never picks a candidate by accident.)
        acceptSuggestionOnEnter: "off",
        tabCompletion: "on",
        suggestOnTriggerCharacters: true,
        suggest: { preview: false, showStatusBar: true, insertMode: "replace" },
      }}
    />
  );
}
