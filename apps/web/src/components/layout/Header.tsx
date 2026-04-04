import Link from "next/link";

export function Header() {
  return (
    <header className="flex h-14 items-center justify-between border-b px-4">
      <div className="flex items-center gap-6">
        <Link href="/" className="font-semibold">
          Agent OS
        </Link>
        <nav className="flex items-center gap-4 text-sm text-muted-foreground">
          <Link href="/agents" className="hover:text-foreground">
            Agents
          </Link>
          <Link href="/flows" className="hover:text-foreground">
            Flows
          </Link>
        </nav>
      </div>
      <div className="text-sm text-muted-foreground">Agent OS v0.1</div>
    </header>
  );
}
