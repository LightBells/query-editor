// Custom Monaco language for the function-style monasql DSL:
//   • Monarch tokenizer (syntax highlight)
//   • dark theme
//   • context-aware autocomplete (tables in from()/join(), columns after alias.,
//     methods after ).)
//   • helper to push compiler errors as inline markers

import type * as Monaco from "monaco-editor";
import type { ColumnDef } from "../types";
import { resolveAlias } from "./aliasResolver";

export const LANG_ID = "monasql";

export interface SchemaAccess {
  tables: () => { name: string; rowCount?: number | null }[];
  columns: (table: string) => ColumnDef[];
}
let schemaAccess: SchemaAccess = { tables: () => [], columns: () => [] };
export function setSchemaAccess(a: SchemaAccess) {
  schemaAccess = a;
}

const BLOCK_KEYWORDS = ["QUERY", "PREDICATE"];
const OP_WORDS = ["and", "or", "not"];
const CONST_WORDS = ["true", "false", "null"];

const BUILDERS = [
  "from", "join", "where", "select", "group_by", "having", "order_by",
  "limit", "offset", "distinct", "agg_lateral", "agg_lateral_grouped",
  "apply", "lateral", "union", "intersect", "except", "with_cte",
];
const FUNCTIONS = [
  "count", "sum", "avg", "min", "max", "string_agg",
  "coalesce", "nullif", "greatest", "least", "concat",
  "row_number", "rank", "dense_rank", "ntile", "lag", "lead",
  "first_value", "last_value", "col", "lit", "star", "raw", "exists",
  "case", "when", "func",
];
const PREDICATES = ["is_active", "in_date_range", "no_outliers", "value_in_range", "has_value"];
const METHODS = [
  "alias", "asc", "desc", "cast", "is_null", "is_not_null",
  "like", "ilike", "in_", "not_in", "between", "over",
];
const NO_ARG_METHODS = new Set(["asc", "desc", "is_null", "is_not_null"]);

// after one of these, `(` opens a table/query argument
const SOURCE_FNS = "(?:from|join|agg_lateral|agg_lateral_grouped|apply|lateral)_?";

export function registerMonasql(monaco: typeof Monaco) {
  if (monaco.languages.getLanguages().some((l) => l.id === LANG_ID)) return;
  monaco.languages.register({ id: LANG_ID });

  monaco.languages.setLanguageConfiguration(LANG_ID, {
    comments: { lineComment: "--" },
    brackets: [["(", ")"], ["[", "]"]],
    autoClosingPairs: [
      { open: "(", close: ")" },
      { open: "[", close: "]" },
      { open: "'", close: "'" },
    ],
    surroundingPairs: [
      { open: "(", close: ")" },
      { open: "[", close: "]" },
      { open: "'", close: "'" },
    ],
  });

  monaco.languages.setMonarchTokensProvider(LANG_ID, {
    blockKeywords: BLOCK_KEYWORDS,
    opWords: OP_WORDS,
    constWords: CONST_WORDS,
    builders: BUILDERS,
    functions: FUNCTIONS,
    predicates: PREDICATES,
    tokenizer: {
      root: [
        [/--.*$/, "comment"],
        [/'(?:[^'\\]|\\.|'')*'/, "string"],
        [/"(?:[^"\\]|\\.)*"/, "string"],
        [/`[^`]*`/, "type"], // back-ticked identifier path: `proj.dataset.table`
        [/\b\d+(\.\d+)?\b/, "number"],
        [
          /[A-Za-z_]\w*/,
          {
            cases: {
              "@blockKeywords": "keyword",
              "@opWords": "keyword",
              "@constWords": "constant",
              "@builders": "predefined",
              "@functions": "predefined",
              "@predicates": "type",
              "@default": "identifier",
            },
          },
        ],
        [/[=<>!~&|]+/, "operator"],
        [/[+\-*/%]/, "operator"],
        [/[(),.:[\]]/, "delimiter"],
      ],
    },
  });

  monaco.editor.defineTheme("monasql-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "keyword", foreground: "cba6f7", fontStyle: "bold" },
      { token: "predefined", foreground: "89b4fa" },
      { token: "type", foreground: "94e2d5" },
      { token: "constant", foreground: "fab387" },
      { token: "string", foreground: "a6e3a1" },
      { token: "number", foreground: "fab387" },
      { token: "comment", foreground: "6c7086", fontStyle: "italic" },
      { token: "operator", foreground: "f5c2e7" },
      { token: "identifier", foreground: "cdd6f4" },
      { token: "delimiter", foreground: "9399b2" },
    ],
    colors: {
      "editor.background": "#1e1e2e",
      "editor.lineHighlightBackground": "#181825",
      "editorLineNumber.foreground": "#45475a",
      "editorCursor.foreground": "#89b4fa",
    },
  });

  monaco.languages.registerCompletionItemProvider(LANG_ID, {
    triggerCharacters: [".", "(", " "],
    provideCompletionItems(model, position) {
      const line = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      });
      const fullText = model.getValue();
      const word = model.getWordUntilPosition(position);
      const range: Monaco.IRange = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      const kinds = monaco.languages.CompletionItemKind;
      const snippetRule = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;

      // 1) `alias.` → that table's columns (a known alias always wins, so
      //    methods never show up for `u.`)
      const dotAlias = line.match(/([A-Za-z_]\w*)\.\s*$/);
      if (dotAlias) {
        const table = resolveAlias(dotAlias[1], fullText);
        if (table) {
          return {
            suggestions: schemaAccess.columns(table).map((c) => ({
              label: c.name,
              kind: kinds.Field,
              insertText: c.name,
              detail: c.type,
              documentation: c.description,
              range,
            })),
          };
        }
      }

      // 2) anything else ending in `.` (e.g. `count(o.id).`) → expression methods
      if (/\.\s*$/.test(line)) {
        return {
          suggestions: METHODS.map((m) => ({
            label: m,
            kind: kinds.Method,
            insertText: NO_ARG_METHODS.has(m) ? `${m}()` : `${m}($0)`,
            insertTextRules: snippetRule,
            range,
          })),
        };
      }

      // 3) inside from()/join()/agg_lateral()/apply() → table & query names
      const re = new RegExp(`\\b${SOURCE_FNS}\\s*\\(\\s*([A-Za-z_]\\w*)?$`);
      if (re.test(line)) {
        return {
          suggestions: schemaAccess.tables().map((t) => ({
            label: t.name,
            kind: kinds.Class,
            insertText: t.name,
            detail: t.rowCount != null ? `${t.rowCount.toLocaleString()} rows` : "table",
            range,
          })),
        };
      }

      // 4) otherwise: builders, functions, predicates, + a QUERY snippet
      const sug: Monaco.languages.CompletionItem[] = [];
      sug.push({
        label: "QUERY",
        kind: kinds.Snippet,
        insertText: ["QUERY ${1:name}:", "  ${2:t} = from(${3:table})", "  select(${4:*})"].join("\n"),
        insertTextRules: snippetRule,
        detail: "query block",
        range,
      });
      for (const b of BUILDERS) {
        sug.push({ label: b, kind: kinds.Function, insertText: `${b}($0)`, insertTextRules: snippetRule, detail: "operation", range });
      }
      for (const f of FUNCTIONS) {
        sug.push({ label: f, kind: kinds.Function, insertText: `${f}($0)`, insertTextRules: snippetRule, detail: "function", range });
      }
      for (const p of PREDICATES) {
        sug.push({ label: p, kind: kinds.Function, insertText: `${p}($0)`, insertTextRules: snippetRule, detail: "predicate", range });
      }
      return { suggestions: sug };
    },
  });
}

export function applyMarkers(
  monaco: typeof Monaco,
  model: Monaco.editor.ITextModel,
  errors: { line: number; col: number; message: string; severity: string }[],
) {
  const markers: Monaco.editor.IMarkerData[] = errors.map((e) => {
    const line = Math.max(1, e.line);
    const col = Math.max(1, e.col);
    return {
      severity:
        e.severity === "warning"
          ? monaco.MarkerSeverity.Warning
          : monaco.MarkerSeverity.Error,
      message: e.message,
      startLineNumber: line,
      startColumn: col,
      endLineNumber: line,
      endColumn: col + 1,
    };
  });
  monaco.editor.setModelMarkers(model, LANG_ID, markers);
}
