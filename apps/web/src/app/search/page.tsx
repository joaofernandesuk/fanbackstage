"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { Discovery } from "../../components/discovery";

function SearchResults() { return <Discovery initialQuery={useSearchParams().get("q") ?? ""} />; }

export default function SearchPage() { return <><h1>Search</h1><Suspense fallback={<p>Loading search…</p>}><SearchResults /></Suspense></>; }
