import { NavLink } from "react-router-dom";
import { Search, Users, Database, Settings } from "lucide-react";

const links = [
  { to: "/", label: "Scrape Profile", icon: Search },
  { to: "/scan", label: "Scan Followers", icon: Users },
  { to: "/database", label: "Database", icon: Database },
  { to: "/admin", label: "Admin", icon: Settings },
];

export default function Navbar() {
  return (
    <nav className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-6xl mx-auto px-4 flex items-center h-14 gap-8">
        <span className="text-lg font-semibold text-white tracking-tight">
          Social Scout
        </span>
        <div className="flex gap-1">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-zinc-800 text-white"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </div>
      </div>
    </nav>
  );
}
