import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import SingleScrape from "./pages/SingleScrape";
import FollowerScan from "./pages/FollowerScan";
import Database from "./pages/Database";
import Admin from "./pages/Admin";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<SingleScrape />} />
        <Route path="/scan" element={<FollowerScan />} />
        <Route path="/database" element={<Database />} />
        <Route path="/admin" element={<Admin />} />
      </Routes>
    </Layout>
  );
}
