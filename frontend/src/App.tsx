import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import SingleScrape from "./pages/SingleScrape";
import FollowerScan from "./pages/FollowerScan";
import KeywordScan from "./pages/KeywordScan";
import Database from "./pages/Database";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<SingleScrape />} />
        <Route path="/scan" element={<FollowerScan />} />
        <Route path="/keyword" element={<KeywordScan />} />
        <Route path="/database" element={<Database />} />
      </Routes>
    </Layout>
  );
}
