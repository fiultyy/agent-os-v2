import { HarnessType } from "@/lib/observe-api";

interface HarnessSelectorProps {
  selectedHarness: HarnessType | null;
  onSelect: (harness: HarnessType) => void;
  onSessionChange: (session: null) => void;
}

const HARNESS_OPTIONS: { value: HarnessType; label: string; description: string }[] = [
  {
    value: "claude-code",
    label: "Claude Code",
    description: "Claude Code CLI harness",
  },
  {
    value: "openclaw",
    label: "OpenClaw",
    description: "OpenClaw agent framework",
  },
  {
    value: "agent-os-v2",
    label: "Agent OS v2",
    description: "Agent OS orchestrator",
  },
];

export function HarnessSelector({
  selectedHarness,
  onSelect,
  onSessionChange,
}: HarnessSelectorProps) {
  const handleSelect = (harness: HarnessType) => {
    onSelect(harness);
    onSessionChange(null); // Clear session when switching harness
  };

  return (
    <div>
      <div className="mb-3 text-sm font-semibold text-gray-700">Harness</div>
      <div className="space-y-2">
        {HARNESS_OPTIONS.map((option) => (
          <button
            key={option.value}
            onClick={() => handleSelect(option.value)}
            className={`w-full rounded-lg border p-3 text-left transition-colors ${
              selectedHarness === option.value
                ? "border-blue-500 bg-blue-50"
                : "border-gray-200 bg-white hover:bg-gray-50"
            }`}
          >
            <div className="font-medium text-sm">{option.label}</div>
            <div className="mt-1 text-xs text-gray-500">{option.description}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
