"use client";

import { memo, useMemo } from "react";
import { Handle, Position } from "@xyflow/react";
import { ThumbsUp, ThumbsDown, Minus, Users } from "lucide-react";

// ── Types ────────────────────────────────────────────────────

export interface CommitteeVoteData {
  /** Parsed from committee.vote event data */
  votes: VoteEntry[];
  proposal: string;
  decision: "accept" | "reject" | "revise";
}

interface VoteEntry {
  role: "Transcriber" | "Refiner" | "Architect";
  vote: "accept" | "reject" | "revise";
  confidence: number;
  comment?: string;
}

// ── Helpers ──────────────────────────────────────────────────

function voteIcon(vote: VoteEntry["vote"]) {
  if (vote === "accept") return <ThumbsUp className="h-3.5 w-3.5 text-green-600" />;
  if (vote === "reject") return <ThumbsDown className="h-3.5 w-3.5 text-red-500" />;
  return <Minus className="h-3.5 w-3.5 text-amber-500" />;
}

function voteBg(vote: VoteEntry["vote"]) {
  if (vote === "accept") return "bg-green-50 border-green-200";
  if (vote === "reject") return "bg-red-50 border-red-200";
  return "bg-amber-50 border-amber-200";
}

function decisionColor(decision: CommitteeVoteData["decision"]) {
  if (decision === "accept") return "text-green-700 bg-green-100";
  if (decision === "reject") return "text-red-700 bg-red-100";
  return "text-amber-700 bg-amber-100";
}

// ── CommitteeVoteNode ────────────────────────────────────────
// @internal — Used by TickCanvas for committee.vote event rendering.
// Not yet wired to the main Canvas flow (reserved for V2 event pipeline).

export interface CommitteeVoteNodeData {
  voteData: CommitteeVoteData;
  lod?: number;
}

export const CommitteeVoteNode = memo(function CommitteeVoteNode(props: {
  data: CommitteeVoteNodeData;
  id: string;
  dragging?: boolean;
}) {
  const { voteData, lod } = props.data;
  const { votes, proposal, decision } = voteData;

  const acceptCount = useMemo(() => votes.filter(v => v.vote === "accept").length, [votes]);
  const rejectCount = useMemo(() => votes.filter(v => v.vote === "reject").length, [votes]);

  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div className="min-w-[220px] rounded-xl border-2 border-indigo-300 bg-white shadow-lg overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-2 bg-indigo-50 border-b border-indigo-200">
          <Users className="h-4 w-4 text-indigo-600" />
          <span className="text-xs font-bold text-indigo-800">Committee Vote</span>
          <span className={`ml-auto px-1.5 py-0.5 text-[10px] font-semibold rounded ${decisionColor(decision)}`}>
            {decision.toUpperCase()}
          </span>
        </div>

        {/* Proposal (L2+) */}
        {(lod === undefined || lod >= 2) && proposal && (
          <div className="px-3 py-1.5 border-b border-gray-100 bg-gray-50">
            <div className="text-[10px] font-semibold uppercase text-gray-400">Proposal</div>
            <div className="text-xs text-gray-700 line-clamp-2 mt-0.5">{proposal}</div>
          </div>
        )}

        {/* Votes */}
        <div className="p-2 space-y-1">
          {votes.map((v, i) => (
            <div key={i} className={`flex items-center gap-2 px-2 py-1 rounded border ${voteBg(v.vote)}`}>
              {voteIcon(v.vote)}
              <span className="text-xs font-medium text-gray-700 flex-1">{v.role}</span>
              <span className="text-[10px] text-gray-500">
                {v.confidence != null ? `${(v.confidence * 100).toFixed(0)}%` : ""}
              </span>
            </div>
          ))}
        </div>

        {/* Summary bar (L2+) */}
        {(lod === undefined || lod >= 2) && (
          <div className="px-3 py-1.5 bg-gray-50 border-t border-gray-100 flex items-center justify-between text-[10px] text-gray-500">
            <span className="text-green-600 font-medium">{acceptCount} accept</span>
            <span className="text-red-500 font-medium">{rejectCount} reject</span>
            <span className="text-amber-500 font-medium">{votes.length - acceptCount - rejectCount} revise</span>
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
});
