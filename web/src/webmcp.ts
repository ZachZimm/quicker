import { useEffect } from "react";
import type { Transaction } from "./api";

type Tool = {
  name: string;
  description: string;
  inputSchema: object;
  annotations: object;
  execute: (input: unknown) => unknown;
};
type ModelDocument = Document & {
  modelContext?: {
    registerTool: (
      tool: Tool,
      options: { signal: AbortSignal },
    ) => void | Promise<void>;
  };
};

export function useReviewTools(
  rows: Transaction[],
  setFilter: (value: string) => void,
) {
  useEffect(() => {
    const context = (document as ModelDocument).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    const tools: Tool[] = [
      {
        name: "list_review_transactions",
        description:
          "Read the proposed transactions and unresolved review fields in this signed-in workspace. Does not approve or enter transactions.",
        inputSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        annotations: { readOnlyHint: true, untrustedContentHint: true },
        execute: () =>
          rows.map((r) => ({
            id: r.id,
            status: r.status,
            payee: r.data.payee,
            amount_minor: r.data.amount_minor,
            date: r.data.date,
            issues: r.issues,
          })),
      },
      {
        name: "show_transaction_status",
        description:
          "Navigate the visible review table to a status filter. Does not change transaction data.",
        inputSchema: {
          type: "object",
          properties: {
            status: {
              type: "string",
              enum: ["review", "approved", "existing", "removed", "all"],
            },
          },
          required: ["status"],
          additionalProperties: false,
        },
        annotations: { readOnlyHint: true, untrustedContentHint: false },
        execute: (input) => {
          const status = (input as { status?: unknown })?.status;
          if (
            typeof status !== "string" ||
            !["review", "approved", "existing", "removed", "all"].includes(
              status,
            )
          )
            throw new Error("Choose a valid transaction status");
          setFilter(status);
          return { status };
        },
      },
    ];
    for (const tool of tools) {
      try {
        void Promise.resolve(
          context.registerTool(tool, { signal: lifecycle.signal }),
        ).catch(() => {});
      } catch {
        /* Optional browser capability. */
      }
    }
    return () => lifecycle.abort();
  }, [rows, setFilter]);
}
