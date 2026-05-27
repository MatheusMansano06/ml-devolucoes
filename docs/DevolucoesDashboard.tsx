import { Bell, CheckCircle2, CircleAlert, CircleHelp, CircleUserRound, LogOut, PackageSearch, RefreshCw, Sparkles, Truck, Wrench } from "lucide-react";
import { useMemo, useState, type ComponentType } from "react";

type QueueCard = {
  id: string;
  label: string;
  value: number;
  tag: string;
  accent: string;
  icon: ComponentType<{ className?: string }>;
};

const queueCards: QueueCard[] = [
  { id: "review-main", label: "Para sua revisao", value: 27, tag: "Atencao necessaria", accent: "blue", icon: CircleAlert },
  { id: "review-fast", label: "Para sua revisao", value: 1, tag: "Revisao rapida", accent: "green", icon: CheckCircle2 },
  { id: "post-office", label: "Para retirar no correio", value: 2, tag: "Aguardando envio", accent: "orange", icon: Truck },
  { id: "others", label: "Outros problemas", value: 24, tag: "Demais pendencias", accent: "purple", icon: Wrench },
];

const accentStyles: Record<string, string> = {
  blue: "border-blue-100 bg-blue-50/50 text-blue-700",
  green: "border-emerald-100 bg-emerald-50/50 text-emerald-700",
  orange: "border-orange-100 bg-orange-50/50 text-orange-700",
  purple: "border-violet-100 bg-violet-50/50 text-violet-700",
};

function Header() {
  return (
    <header className="border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-16 w-full max-w-[1320px] items-center justify-between px-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-2 text-slate-900">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50">
            <PackageSearch className="h-4 w-4 text-blue-600" />
          </div>
          <span className="text-sm font-semibold tracking-tight sm:text-base">Mercado Livre</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 text-slate-600 transition hover:bg-slate-50"
            aria-label="Notificacoes"
          >
            <Bell className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3.5 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            <LogOut className="h-4 w-4" />
            Sair
          </button>
        </div>
      </div>
    </header>
  );
}

function PageTitle() {
  return (
    <section className="space-y-2">
      <p className="inline-flex items-center gap-1 rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-semibold tracking-wide text-blue-700">
        <Sparkles className="h-3.5 w-3.5" />
        BEM-VINDO DE VOLTA! 👋
      </p>
      <h1 className="text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
        Gerencie suas <span className="text-blue-600">devolucoes</span>
      </h1>
      <p className="max-w-3xl text-sm text-slate-600 sm:text-base">
        Acompanhe, revise e resolva pendencias de devolucao de forma rapida e inteligente.
      </p>
    </section>
  );
}

function ReturnEntryCard() {
  const [identifierType, setIdentifierType] = useState("ID ML");
  const [query, setQuery] = useState("");

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-3">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-blue-600">Entrada de devolucao</p>
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Leia ou digite o ID do pedido</h2>
          <p className="text-sm text-slate-600">
            Use a pistola de QR code/codigo de barras ou digite o numero manualmente.
          </p>
        </div>
        <div className="space-y-3">
          <div className="flex flex-col gap-3 sm:flex-row">
            <div className="relative w-full sm:max-w-[150px]">
              <CircleUserRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <select
                value={identifierType}
                onChange={(event) => setIdentifierType(event.target.value)}
                className="h-12 w-full rounded-2xl border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm font-medium text-slate-700 outline-none ring-blue-500 transition focus:ring-2"
              >
                <option>ID ML</option>
              </select>
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Pedido, pacote ou rastreio"
              className="h-12 w-full rounded-2xl border border-slate-200 bg-white px-4 text-sm text-slate-800 outline-none ring-blue-500 transition placeholder:text-slate-400 focus:ring-2"
            />
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              className="inline-flex h-12 items-center justify-center rounded-2xl bg-blue-600 px-5 text-sm font-semibold text-white transition hover:bg-blue-700"
            >
              Buscar venda
            </button>
            <button
              type="button"
              className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
            >
              <RefreshCw className="h-4 w-4" />
              Atualizar ML
            </button>
          </div>
        </div>
      </div>
      <div className="mt-5 flex items-start gap-2 rounded-2xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
        <p>Progresso salvo! Voce pode continuar depois.</p>
      </div>
    </section>
  );
}

function QueueCardItem({ card }: { card: QueueCard }) {
  const Icon = card.icon;
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:shadow-md">
      <div className="mb-4 flex items-center gap-3">
        <div className={`flex h-10 w-10 items-center justify-center rounded-full border ${accentStyles[card.accent]}`}>
          <Icon className="h-4 w-4" />
        </div>
        <p className="text-sm font-medium text-slate-600">{card.label}</p>
      </div>
      <p className="text-3xl font-semibold tracking-tight text-slate-900">{card.value}</p>
      <span className={`mt-3 inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${accentStyles[card.accent]}`}>
        {card.tag}
      </span>
    </article>
  );
}

function UpcomingCard() {
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">Proximas a serem atendidas</h3>
          <p className="mt-1 text-sm text-slate-600">Organize e priorize suas pendencias</p>
        </div>
        <button type="button" className="text-sm font-semibold text-blue-600 hover:text-blue-700">
          Ver todas
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {queueCards.map((card) => (
          <QueueCardItem key={card.id} card={card} />
        ))}
      </div>
    </section>
  );
}

function CircularProgress({ value }: { value: number }) {
  const angle = useMemo(() => Math.round((value / 100) * 360), [value]);
  return (
    <div
      className="grid h-28 w-28 place-items-center rounded-full"
      style={{
        background: `conic-gradient(#2563eb ${angle}deg, #e2e8f0 0deg)`,
      }}
      aria-label={`Progresso ${value}%`}
    >
      <div className="grid h-20 w-20 place-items-center rounded-full bg-white text-lg font-semibold text-slate-900">{value}%</div>
    </div>
  );
}

function PendingCard() {
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="grid gap-6 lg:grid-cols-[1.1fr_auto_1fr] lg:items-center">
        <div className="space-y-3">
          <h3 className="text-lg font-semibold text-slate-900">Pendencias</h3>
          <p className="text-sm text-slate-600">Checklists iniciados</p>
          <button
            type="button"
            className="inline-flex h-11 items-center justify-center rounded-xl border border-slate-200 px-4 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Visualizar pendencias
          </button>
        </div>

        <div className="flex items-center gap-4">
          <CircularProgress value={68} />
          <div>
            <p className="text-sm font-semibold text-slate-900">Em andamento</p>
            <p className="text-sm text-slate-600">Checklists iniciados</p>
            <p className="mt-1 text-xs text-slate-500">Voce tem checklists em progresso aguardando sua acao.</p>
          </div>
        </div>

        <div className="grid grid-cols-3 divide-x divide-slate-200 rounded-2xl border border-slate-200 bg-slate-50/40">
          <div className="px-4 py-4">
            <p className="text-2xl font-semibold text-slate-900">36</p>
            <p className="mt-1 text-xs text-slate-600">Checklists ativos</p>
          </div>
          <div className="px-4 py-4">
            <p className="text-2xl font-semibold text-slate-900">12</p>
            <p className="mt-1 text-xs text-slate-600">Aguardando sua acao</p>
          </div>
          <div className="px-4 py-4">
            <p className="text-2xl font-semibold text-slate-900">4</p>
            <p className="mt-1 text-xs text-slate-600">Perto do vencimento</p>
          </div>
        </div>
      </div>
    </section>
  );
}

function FooterHelp() {
  return (
    <footer className="mt-8 border-t border-slate-200 pt-4">
      <div className="flex flex-col items-center justify-center gap-3 text-center sm:flex-row sm:gap-4">
        <p className="text-sm text-slate-500">Precisa de ajuda? Nossa central de ajuda esta disponivel 24/7.</p>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
        >
          <CircleHelp className="h-4 w-4" />
          Abrir suporte
        </button>
      </div>
    </footer>
  );
}

export default function DevolucoesDashboardPage() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <Header />
      <main className="mx-auto w-full max-w-[1320px] space-y-6 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <PageTitle />
        <ReturnEntryCard />
        <UpcomingCard />
        <PendingCard />
        <FooterHelp />
      </main>
    </div>
  );
}
