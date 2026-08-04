import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";
import { SSEProvider } from "@/components/layout/sse-provider";
import { ClientOnly } from "@/components/layout/client-only";
import { Toaster } from "@/components/ui/toaster";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "Code Tumbler",
  description: "Autonomous code generation and refinement platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // suppressHydrationWarning: the theme toggle sets the `dark` class on <html>
    // and browser extensions (e.g. Dark Reader) inject inline styles/attributes
    // before React hydrates — both are expected client-only mutations.
    <html lang="en" suppressHydrationWarning>
      <body
        suppressHydrationWarning
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <SSEProvider>
          {/* Client-only shell: immune to extension-induced hydration mismatches */}
          <ClientOnly>
            <div className="flex min-h-screen">
              <Sidebar />
              <main className="flex-1 overflow-auto pt-14 md:pt-0">{children}</main>
            </div>
          </ClientOnly>
        </SSEProvider>
        <Toaster />
      </body>
    </html>
  );
}
