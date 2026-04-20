import { useEffect, useState } from "react";
import { Loader2, RefreshCw, Plus, Sparkles, Play, AlertCircle, CheckCircle2, XCircle } from "lucide-react";
import { adminApi } from "../lib/api";
import type { City, Anchor, AdminUser, AdminState, SchemaCheck } from "../lib/api";

export default function Admin() {
  const [schema, setSchema] = useState<SchemaCheck | null>(null);
  const [state, setState] = useState<AdminState | null>(null);
  const [cities, setCities] = useState<City[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);

  const [selectedCityId, setSelectedCityId] = useState<string | null>(null);
  const [anchors, setAnchors] = useState<Anchor[]>([]);

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [userRecs, setUserRecs] = useState<Awaited<ReturnType<typeof adminApi.userRecommendations>> | null>(null);

  const [loading, setLoading] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "success" | "error"; msg: string } | null>(null);

  // form state
  const [cityForm, setCityForm] = useState({ name: "", state: "", lat: "", lng: "" });
  const [maxAnchors, setMaxAnchors] = useState<string>("");
  const [userForm, setUserForm] = useState({ email: "", name: "", city_id: "", lat: "", lng: "" });

  const refreshAll = async () => {
    try {
      const [s, st, c, u] = await Promise.all([
        adminApi.schemaCheck(),
        adminApi.state(),
        adminApi.listCities(),
        adminApi.listUsers(),
      ]);
      setSchema(s);
      setState(st);
      setCities(c.cities);
      setUsers(u.users);
    } catch (e) {
      setToast({ kind: "error", msg: (e as Error).message });
    }
  };

  useEffect(() => {
    refreshAll();
  }, []);

  useEffect(() => {
    if (!selectedCityId) {
      setAnchors([]);
      return;
    }
    adminApi.listAnchors(selectedCityId).then((r) => setAnchors(r.anchors)).catch((e) =>
      setToast({ kind: "error", msg: (e as Error).message })
    );
  }, [selectedCityId]);

  useEffect(() => {
    if (!selectedUserId) {
      setUserRecs(null);
      return;
    }
    adminApi.userRecommendations(selectedUserId).then(setUserRecs).catch((e) =>
      setToast({ kind: "error", msg: (e as Error).message })
    );
  }, [selectedUserId]);

  const run = async (key: string, fn: () => Promise<unknown>, successMsg?: string) => {
    setLoading(key);
    setToast(null);
    try {
      const result = await fn();
      setToast({
        kind: "success",
        msg: successMsg || `Done: ${JSON.stringify((result as { stats?: unknown }).stats ?? result)}`,
      });
      await refreshAll();
      if (selectedCityId) adminApi.listAnchors(selectedCityId).then((r) => setAnchors(r.anchors));
      if (selectedUserId) adminApi.userRecommendations(selectedUserId).then(setUserRecs);
    } catch (e) {
      setToast({ kind: "error", msg: (e as Error).message });
    } finally {
      setLoading(null);
    }
  };

  const handleCreateCity = (e: React.FormEvent) => {
    e.preventDefault();
    run(
      "createCity",
      () =>
        adminApi.createCity({
          name: cityForm.name.trim(),
          state: cityForm.state.trim(),
          lat: cityForm.lat ? Number(cityForm.lat) : undefined,
          lng: cityForm.lng ? Number(cityForm.lng) : undefined,
        }),
      "City created"
    ).then(() => setCityForm({ name: "", state: "", lat: "", lng: "" }));
  };

  const handleCreateUser = (e: React.FormEvent) => {
    e.preventDefault();
    run(
      "createUser",
      () =>
        adminApi.createUser({
          email: userForm.email.trim(),
          name: userForm.name.trim() || undefined,
          city_id: userForm.city_id,
          lat: userForm.lat ? Number(userForm.lat) : undefined,
          lng: userForm.lng ? Number(userForm.lng) : undefined,
        }),
      "User created"
    ).then(() => setUserForm({ email: "", name: "", city_id: "", lat: "", lng: "" }));
  };

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white mb-2">Recommendation Admin</h1>
          <p className="text-zinc-400 text-sm">
            Exercise the full pipeline end-to-end: create a city → seed anchors →
            create a user → run drop prep → run the drop → review recommendations.
          </p>
        </div>
        <button
          onClick={refreshAll}
          className="flex items-center gap-2 bg-zinc-900 border border-zinc-700 text-zinc-200 px-3 py-2 rounded-lg text-sm hover:bg-zinc-800"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {toast && (
        <div
          className={`flex items-start gap-2 p-3 rounded-lg border text-sm ${
            toast.kind === "success"
              ? "bg-emerald-950/40 border-emerald-800 text-emerald-200"
              : "bg-red-950/40 border-red-800 text-red-200"
          }`}
        >
          {toast.kind === "success" ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
          <div className="whitespace-pre-wrap break-words">{toast.msg}</div>
        </div>
      )}

      {/* Schema check */}
      <section className="bg-zinc-950 border border-zinc-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-2">Schema</h2>
        {schema ? (
          <div className="flex flex-wrap gap-2 text-xs">
            {Object.entries(schema.schema).map(([k, v]) => (
              <span
                key={k}
                className={`px-2 py-1 rounded-md border flex items-center gap-1 ${
                  v
                    ? "bg-emerald-950/40 border-emerald-800 text-emerald-200"
                    : "bg-red-950/40 border-red-800 text-red-200"
                }`}
              >
                {v ? <CheckCircle2 size={12} /> : <XCircle size={12} />} {k}
              </span>
            ))}
          </div>
        ) : (
          <div className="text-zinc-500 text-sm">Loading…</div>
        )}
        {schema && !schema.ready && (
          <div className="mt-3 text-xs text-zinc-400">
            Missing tables/functions. Run{" "}
            <code className="text-zinc-300">
              supabase/migrations/001_recommendation_schema.sql
            </code>{" "}
            in your Supabase SQL editor.
          </div>
        )}
      </section>

      {/* Stats */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {state &&
          Object.entries(state).map(([k, v]) => (
            <div key={k} className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
              <div className="text-xs text-zinc-500 uppercase tracking-wide">
                {k.replace(/_/g, " ")}
              </div>
              <div className="text-xl font-bold text-white mt-1">{v}</div>
            </div>
          ))}
      </section>

      {/* Pipeline actions */}
      <section className="bg-zinc-950 border border-zinc-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-3">Run pipeline</h2>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => run("prep", adminApi.runDropPrep)}
            disabled={loading !== null}
            className="flex items-center gap-2 bg-amber-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-amber-500 disabled:opacity-50"
          >
            {loading === "prep" ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            Run drop prep (spends credits)
          </button>
          <button
            onClick={() => run("drop", adminApi.runDrop)}
            disabled={loading !== null}
            className="flex items-center gap-2 bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
          >
            {loading === "drop" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            Run drop NOW
          </button>
        </div>
        <p className="text-xs text-zinc-500 mt-2">
          Drop prep synchronously harvests anchors + analyzes followers until every
          eligible user has ≥10 candidates. Run drop then inserts up to 10
          recommendations per eligible user.
        </p>
      </section>

      {/* Max anchors — testing knob */}
      <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-zinc-950 border border-zinc-800">
        <label className="text-xs text-zinc-400 whitespace-nowrap">
          Max anchors per seed (blank = all)
        </label>
        <input
          type="number"
          min={1}
          value={maxAnchors}
          onChange={(e) => setMaxAnchors(e.target.value)}
          placeholder="e.g. 3"
          className="flex-1 max-w-[120px] bg-zinc-900 border border-zinc-700 text-white text-sm px-2 py-1 rounded-md focus:outline-none focus:border-indigo-500"
        />
        <span className="text-xs text-zinc-500">
          Stops seeding once this many eligible anchors have been inserted. Saves money while testing.
        </span>
      </div>

      {/* Cities */}
      <section className="bg-zinc-950 border border-zinc-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-3">Cities</h2>

        <form onSubmit={handleCreateCity} className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-4">
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Name (e.g. Austin)"
            value={cityForm.name}
            onChange={(e) => setCityForm({ ...cityForm, name: e.target.value })}
            required
          />
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="State (e.g. TX)"
            value={cityForm.state}
            onChange={(e) => setCityForm({ ...cityForm, state: e.target.value })}
            required
          />
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Lat"
            type="number"
            step="any"
            value={cityForm.lat}
            onChange={(e) => setCityForm({ ...cityForm, lat: e.target.value })}
          />
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Lng"
            type="number"
            step="any"
            value={cityForm.lng}
            onChange={(e) => setCityForm({ ...cityForm, lng: e.target.value })}
          />
          <button
            type="submit"
            disabled={loading !== null}
            className="flex items-center justify-center gap-2 bg-white text-zinc-900 px-3 py-2 rounded-lg text-sm font-medium hover:bg-zinc-200 disabled:opacity-50"
          >
            <Plus size={14} /> Add
          </button>
        </form>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-zinc-500 border-b border-zinc-800">
              <tr>
                <th className="text-left py-2 px-2">Name</th>
                <th className="text-left py-2 px-2">State</th>
                <th className="text-left py-2 px-2">Status</th>
                <th className="text-left py-2 px-2">Lat/Lng</th>
                <th className="text-right py-2 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {cities.map((c) => (
                <tr
                  key={c.id}
                  className={`border-b border-zinc-800/40 ${
                    selectedCityId === c.id ? "bg-zinc-900/40" : ""
                  }`}
                >
                  <td className="py-2 px-2 text-white">{c.name}</td>
                  <td className="py-2 px-2 text-zinc-300">{c.state}</td>
                  <td className="py-2 px-2">
                    <span
                      className={`px-2 py-0.5 rounded-md text-xs ${
                        c.status === "active"
                          ? "bg-emerald-950/40 text-emerald-200 border border-emerald-800"
                          : "bg-zinc-900 text-zinc-400 border border-zinc-700"
                      }`}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-xs text-zinc-500">
                    {c.lat != null && c.lng != null ? `${c.lat}, ${c.lng}` : "—"}
                  </td>
                  <td className="py-2 px-2 text-right">
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => setSelectedCityId(c.id)}
                        className="text-xs text-zinc-300 hover:text-white px-2 py-1 rounded-md border border-zinc-700 hover:bg-zinc-800"
                      >
                        anchors
                      </button>
                      <button
                        onClick={() => run(
                          `seed-${c.id}`,
                          () => adminApi.seedCity(c.id, maxAnchors ? Number(maxAnchors) : undefined),
                          "Seed complete"
                        )}
                        disabled={loading !== null}
                        className="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-2 py-1 rounded-md flex items-center gap-1 disabled:opacity-50"
                      >
                        {loading === `seed-${c.id}` ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          <Sparkles size={12} />
                        )}
                        seed
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {cities.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-zinc-500 text-sm">
                    No cities yet. Create one above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Anchors for selected city */}
        {selectedCityId && (
          <div className="mt-4 border-t border-zinc-800 pt-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-white">
                Anchors for {cities.find((c) => c.id === selectedCityId)?.name}
              </h3>
              <button
                onClick={() => setSelectedCityId(null)}
                className="text-xs text-zinc-400 hover:text-zinc-200"
              >
                close
              </button>
            </div>
            <div className="overflow-x-auto max-h-96 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="text-zinc-500 border-b border-zinc-800 sticky top-0 bg-zinc-950">
                  <tr>
                    <th className="text-left py-1 px-2">@handle</th>
                    <th className="text-left py-1 px-2">Category</th>
                    <th className="text-right py-1 px-2">Followers</th>
                    <th className="text-left py-1 px-2">Status</th>
                    <th className="text-left py-1 px-2">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {anchors.map((a) => (
                    <tr key={a.id} className="border-b border-zinc-800/30">
                      <td className="py-1 px-2 text-white">@{a.username}</td>
                      <td className="py-1 px-2 text-zinc-300">{a.category}</td>
                      <td className="py-1 px-2 text-right text-zinc-300">
                        {a.follower_count?.toLocaleString() ?? "—"}
                      </td>
                      <td className="py-1 px-2 text-zinc-300">{a.status}</td>
                      <td className="py-1 px-2 text-zinc-500">{a.invalid_reason ?? ""}</td>
                    </tr>
                  ))}
                  {anchors.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-3 text-center text-zinc-500">
                        No anchors for this city. Click "seed" above.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* Users */}
      <section className="bg-zinc-950 border border-zinc-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-3">Test users</h2>

        <form onSubmit={handleCreateUser} className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-4">
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Email"
            value={userForm.email}
            onChange={(e) => setUserForm({ ...userForm, email: e.target.value })}
            required
          />
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Name (optional)"
            value={userForm.name}
            onChange={(e) => setUserForm({ ...userForm, name: e.target.value })}
          />
          <select
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            value={userForm.city_id}
            onChange={(e) => setUserForm({ ...userForm, city_id: e.target.value })}
            required
          >
            <option value="">Home city…</option>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}, {c.state}
              </option>
            ))}
          </select>
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Lat"
            type="number"
            step="any"
            value={userForm.lat}
            onChange={(e) => setUserForm({ ...userForm, lat: e.target.value })}
          />
          <input
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white"
            placeholder="Lng"
            type="number"
            step="any"
            value={userForm.lng}
            onChange={(e) => setUserForm({ ...userForm, lng: e.target.value })}
          />
          <button
            type="submit"
            disabled={loading !== null}
            className="flex items-center justify-center gap-2 bg-white text-zinc-900 px-3 py-2 rounded-lg text-sm font-medium hover:bg-zinc-200 disabled:opacity-50"
          >
            <Plus size={14} /> Add
          </button>
        </form>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-zinc-500 border-b border-zinc-800">
              <tr>
                <th className="text-left py-2 px-2">Email</th>
                <th className="text-left py-2 px-2">Name</th>
                <th className="text-left py-2 px-2">City</th>
                <th className="text-left py-2 px-2">First eligible drop</th>
                <th className="text-right py-2 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const city = cities.find((c) => c.id === u.city_id);
                return (
                  <tr
                    key={u.id}
                    className={`border-b border-zinc-800/40 ${
                      selectedUserId === u.id ? "bg-zinc-900/40" : ""
                    }`}
                  >
                    <td className="py-2 px-2 text-white">{u.email}</td>
                    <td className="py-2 px-2 text-zinc-300">{u.name ?? "—"}</td>
                    <td className="py-2 px-2 text-zinc-300">
                      {city ? `${city.name}, ${city.state}` : "—"}
                    </td>
                    <td className="py-2 px-2 text-xs text-zinc-500">
                      {u.first_eligible_drop_at
                        ? new Date(u.first_eligible_drop_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-2 px-2 text-right">
                      <button
                        onClick={() => setSelectedUserId(u.id)}
                        className="text-xs text-zinc-300 hover:text-white px-2 py-1 rounded-md border border-zinc-700 hover:bg-zinc-800"
                      >
                        recs
                      </button>
                    </td>
                  </tr>
                );
              })}
              {users.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-zinc-500 text-sm">
                    No users yet. Add one above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Selected user recommendations */}
        {selectedUserId && userRecs && (
          <div className="mt-4 border-t border-zinc-800 pt-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold text-white">
                  Recommendations for {users.find((u) => u.id === selectedUserId)?.email}
                </h3>
                <p className="text-xs text-zinc-500 mt-0.5">
                  {userRecs.recommendations.length} delivered · {userRecs.available_now ?? "?"} available
                  right now (dry-run)
                </p>
              </div>
              <button
                onClick={() => setSelectedUserId(null)}
                className="text-xs text-zinc-400 hover:text-zinc-200"
              >
                close
              </button>
            </div>

            {userRecs.recommendations.length === 0 ? (
              <div className="text-sm text-zinc-500">
                No recommendations delivered yet. Run drop prep, then run drop.
              </div>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {userRecs.recommendations.slice(0, 10).map((r) => (
                  <div
                    key={r.profile_username}
                    className="bg-zinc-900 border border-zinc-800 rounded-lg p-2 flex flex-col gap-1"
                  >
                    {r.profile_image_url && (
                      <img
                        src={r.profile_image_url}
                        alt={r.profile_username}
                        className="w-full aspect-square object-cover rounded-md bg-zinc-800"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = "none";
                        }}
                      />
                    )}
                    <div className="text-xs text-white truncate">@{r.profile_username}</div>
                    <div className="text-[10px] text-zinc-500">
                      #{r.rank} · {r.drop_date}
                    </div>
                    {r.profile?.attractiveness_rating != null && (
                      <div className="text-[10px] text-zinc-400">
                        rating: {r.profile.attractiveness_rating.toFixed(2)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <p className="text-xs text-zinc-600 text-center py-2">
        Typical flow: 1) Add city (with lat/lng) → 2) Seed city → 3) Add test user in
        that city → 4) Run drop prep → 5) Run drop → 6) Click the user's "recs"
        button to see the delivered 10.
      </p>
    </div>
  );
}
