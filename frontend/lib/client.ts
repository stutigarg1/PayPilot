import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const supabase = supabaseUrl && supabaseKey
  ? createClient(supabaseUrl, supabaseKey)
  : null;

export const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Payment = {
  id: string;
  amount: string;
  currency: string;
  description: string;
  status: "DRAFT" | "CREATING" | "CREATED" | "CAPTURE_PENDING" | "COMPLETED";
  created_at: string;
  completed_at: string | null;
  order_id: string | null;
  capture_id: string | null;
  approval_url: string | null;
  risks: string[];
  duplicate: boolean;
};

export type Health = {
  status: string;
  environment: string;
  auth_mode: "local" | "supabase";
  parser: "gemini" | "rule-based";
  paypal_configured: boolean;
};

export type Summary = {
  completed_count: number;
  totals: Record<string, string>;
  label: string;
};

export function money(amount: string, currency: string) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(amount));
}
