"""Popula o Fleetbase local com dados de simulação (São Paulo).

O seed pode ser interrompido e rodado de novo: ele reaproveita o que já existe
(veículos por placa, motoristas por telefone, locais por nome, pedidos pelo número
nas observações) e só cria o que falta.

Uso:
    python seed_fleetbase.py --check          # testa a conexão e a chave
    python seed_fleetbase.py --status         # mostra o que já existe (não altera nada)
    python seed_fleetbase.py                  # cria o que faltar
    python seed_fleetbase.py --extra-driver   # cria só um motorista livre (para a demo)
    python seed_fleetbase.py --reset-orders   # tenta apagar os pedidos da simulação
                                              # (experimental: não confirmei que a API permite)

Variáveis no .env:
    FLEETBASE_API_URL=http://localhost:8000/v1
    FLEETBASE_API_KEY=sua_chave_secreta
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("FLEETBASE_API_URL", "http://localhost:8000/v1").rstrip("/")
KEY = os.getenv("FLEETBASE_API_KEY")
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

SEED_NOTE = "Pedido de simulação (seed)"
DRIVERS = [
    ("Carlos Silva", "+5511987650001"), ("Marina Souza", "+5511987650002"),
    ("João Pereira", "+5511987650003"), ("Ana Costa", "+5511987650004"),
    ("Pedro Lima", "+5511987650005"), ("Beatriz Rocha", "+5511987650006"),
]
EXTRA_DRIVER = ("Rafael Mendes", "+5511987650007")
VEHICLES = [
    ("Fiat", "Fiorino", 2022, "ABC1D23"), ("Renault", "Master", 2021, "DEF2G34"),
    ("Honda", "CG 160", 2023, "GHI3J45"), ("VW", "Saveiro", 2020, "JKL4M56"),
]
# nome, rua, bairro, cep, lat, lng
PLACES = [
    ("Centro de Distribuição", "Av. do Estado, 5533", "Cambuci", "01516-000", -23.5700, -46.6150),
    ("Loja Pinheiros", "Rua dos Pinheiros, 1000", "Pinheiros", "05422-001", -23.5667, -46.6920),
    ("Cliente Paulista", "Av. Paulista, 1578", "Bela Vista", "01310-200", -23.5614, -46.6559),
    ("Cliente Vila Madalena", "Rua Harmonia, 200", "Vila Madalena", "05435-000", -23.5560, -46.6910),
    ("Cliente Moema", "Av. Ibirapuera, 3103", "Moema", "04029-200", -23.6010, -46.6670),
    ("Cliente Tatuapé", "Rua Tuiuti, 2000", "Tatuapé", "03307-000", -23.5400, -46.5760),
    ("Cliente Santo André", "Av. Industrial, 600", "Santo André", "09080-500", -23.6560, -46.5280),
    ("Cliente Lapa", "Rua Clélia, 1000", "Lapa", "05042-000", -23.5230, -46.7020),
]
# (cliente_idx, motorista_idx | None, horas_agendadas, despachar). O motorista 6 fica livre (demo).
PLAN = [(0, None, 24, False), (1, None, 26, False), (2, 0, 3, True), (3, 1, 2, True),
        (4, 2, 1, True), (0, 3, -3, True), (1, 4, -5, True), (2, None, -2, False),
        (3, None, 5, False), (4, None, 48, False)]


def call(method, path, body=None):
    """Chama a API. Em erro, mostra o motivo e devolve None (não derruba o script)."""
    try:
        r = requests.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    except requests.RequestException as e:
        print(f"  ✖ {method} {path}: sem conexão ({e})")
        return None
    if r.status_code >= 400:
        print(f"  ✖ {method} {path}: HTTP {r.status_code} -> {r.text[:300]}")
        return None
    return r.json() if r.text else {}


def items(resp):
    if isinstance(resp, dict):
        return resp.get("data", [])
    return resp or []


def norm(texto):
    """Minúsculas e sem acentos (o Fleetbase muda a caixa dos nomes ao gravar)."""
    sem = "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                  if unicodedata.category(c) != "Mn")
    return sem.casefold().strip()


def digits(texto):
    return re.sub(r"\D", "", str(texto or ""))


def listar(path):
    """Lista registros. Se a listagem falhar, aborta: criar às cegas poderia duplicar dados."""
    r = call("GET", f"{path}?limit=500")
    if r is None:
        sys.exit(f"Não consegui listar {path}. Abortando para não duplicar dados.")
    return items(r)


def check():
    print(f"Testando {BASE}/drivers ...")
    resp = call("GET", "/drivers")
    if resp is None:
        return False
    print(f"  ✔ API respondeu. Motoristas existentes: {len(items(resp))}")
    return True


def pedidos_da_simulacao():
    """(por_indice, legados): pedidos criados por este seed. Legados = sem número nas observações."""
    por_indice, legados = {}, []
    for o in listar("/orders"):
        notas = str(o.get("notes") or "")
        if not notas.startswith(SEED_NOTE):
            continue
        m = re.search(r"#(\d+)$", notas)
        if m:
            por_indice[int(m.group(1))] = o
        else:
            legados.append(o)
    return por_indice, legados


def status():
    placas = {norm(p) for *_, p in VEHICLES}
    fones = {digits(f) for _, f in DRIVERS + [EXTRA_DRIVER]}
    nomes = {norm(p[0]) for p in PLACES}
    v = [x for x in listar("/vehicles") if norm(x.get("plate_number")) in placas]
    d = [x for x in listar("/drivers") if digits(x.get("phone")) in fones]
    p = [x for x in listar("/places") if norm(x.get("name")) in nomes]
    por_indice, legados = pedidos_da_simulacao()
    print(f"Veículos da simulação: {len(v)}/{len(VEHICLES)}")
    print(f"Motoristas da simulação: {len(d)}/{len(DRIVERS)} (+ extra opcional)")
    print(f"Locais da simulação: {len(p)}/{len(PLACES)}")
    print(f"Pedidos da simulação: {len(por_indice)}/{len(PLAN)}"
          + (f" (+ {len(legados)} antigos sem número)" if legados else ""))


def seed_base():
    created = {"vehicles": [], "drivers": [], "places": [], "orders": []}

    print("Veículos...")
    por_placa, por_modelo = {}, {}
    for v in listar("/vehicles"):
        por_placa[norm(v.get("plate_number"))] = v
        por_modelo[(norm(v.get("make")), norm(v.get("model")))] = v
    for make, model, year, plate in VEHICLES:
        v = por_placa.get(norm(plate)) or por_modelo.get((norm(make), norm(model)))
        if v:
            print(f"  = {plate} já existia")
            created["vehicles"].append(v["id"])
            continue
        r = call("POST", "/vehicles", {"make": make, "model": model, "year": str(year),
                                       "plate_number": plate, "status": "active"})
        if r:
            print(f"  + {plate} criado")
            created["vehicles"].append(r["id"])

    print("Motoristas...")
    por_fone = {digits(d.get("phone")): d for d in listar("/drivers")}
    for i, (name, phone) in enumerate(DRIVERS):
        d = por_fone.get(digits(phone))
        if d:
            print(f"  = {name} já existia")
            created["drivers"].append(d["id"])
            continue
        body = {"name": name, "phone": phone, "status": "active", "country": "BR",
                "city": "São Paulo"}
        if i < len(created["vehicles"]):
            body["vehicle"] = created["vehicles"][i]
        r = call("POST", "/drivers", body)
        if r:
            print(f"  + {name} criado")
            created["drivers"].append(r["id"])

    print("Locais...")
    por_nome = {norm(p.get("name")): p for p in listar("/places")}
    for name, street, hood, cep, lat, lng in PLACES:
        p = por_nome.get(norm(name))
        if p:
            print(f"  = {name} já existia")
            created["places"].append(p["id"])
            continue
        r = call("POST", "/places", {"name": name, "street1": street, "neighborhood": hood,
                                     "city": "São Paulo", "province": "SP", "postal_code": cep,
                                     "country": "BR", "latitude": lat, "longitude": lng})
        if r:
            print(f"  + {name} criado")
            created["places"].append(r["id"])
    return created


def seed_orders(created):
    if len(created["places"]) < len(PLACES) or not created["drivers"]:
        print("Faltam locais ou motoristas para criar pedidos. Veja os erros acima e rode de novo.")
        return created

    print("Pedidos...")
    por_indice, legados = pedidos_da_simulacao()
    if legados:
        print(f"  ⚠ {len(legados)} pedido(s) de simulação antigos (sem número) foram mantidos. "
              "Apague-os pelo console se não quiser vê-los.")
    now = datetime.now(timezone.utc)
    origin, customers = created["places"][0], created["places"][2:]
    for i, (cust, drv, hours, dispatch) in enumerate(PLAN):
        existente = por_indice.get(i)
        if existente:
            tem_motorista = isinstance(existente.get("driver_assigned"), dict)
            if dispatch and tem_motorista and (existente.get("status") or "").lower() == "created":
                call("PATCH", f"/orders/{existente['id']}/dispatch")   # retoma despacho interrompido
                print(f"  ~ #{i} já existia; despacho concluído")
            else:
                print(f"  = #{i} já existia")
            created["orders"].append(existente["id"])
            continue
        body = {"pickup": origin, "dropoff": customers[cust % len(customers)],
                "type": "transport",
                "scheduled_at": (now + timedelta(hours=hours)).isoformat(),
                "notes": f"{SEED_NOTE} #{i}"}
        if drv is not None and drv < len(created["drivers"]):
            body["driver"] = created["drivers"][drv]
        r = call("POST", "/orders", body)
        if not r:
            continue
        print(f"  + #{i} criado")
        created["orders"].append(r["id"])
        if dispatch and "driver" in body:
            call("PATCH", f"/orders/{r['id']}/dispatch")
    return created


def extra_driver():
    nome, fone = EXTRA_DRIVER
    if any(digits(d.get("phone")) == digits(fone) for d in listar("/drivers")):
        print(f"= {nome} já existe (sem pedidos, livre).")
        return
    r = call("POST", "/drivers", {"name": nome, "phone": fone, "status": "active",
                                  "country": "BR", "city": "São Paulo"})
    print(f"✔ {nome} criado (sem pedidos, ficará livre)." if r else "✖ Não foi possível criar.")


def reset_orders():
    """Tenta apagar os pedidos da simulação pela API. Não confirmei que o endpoint existe."""
    por_indice, legados = pedidos_da_simulacao()
    todos = list(por_indice.values()) + legados
    if not todos:
        print("Nenhum pedido de simulação para apagar.")
        return
    apagados = 0
    for o in todos:
        if call("DELETE", f"/orders/{o['id']}") is not None:
            apagados += 1
    print(f"{apagados}/{len(todos)} pedido(s) apagado(s).")
    if apagados < len(todos):
        print("A API não aceitou apagar. Apague pelo console: Fleet-Ops > Pedidos > "
              "selecionar > excluir. Depois rode o seed de novo.")


if __name__ == "__main__":
    if not KEY:
        sys.exit("Defina FLEETBASE_API_KEY no .env")
    if not check():
        sys.exit("Conexão falhou. Corrija antes de popular.")
    if "--check" in sys.argv:
        sys.exit(0)
    if "--status" in sys.argv:
        status()
        sys.exit(0)
    if "--extra-driver" in sys.argv:
        extra_driver()
        sys.exit(0)
    if "--reset-orders" in sys.argv:
        reset_orders()
        sys.exit(0)
    try:
        result = seed_orders(seed_base())
    except KeyboardInterrupt:
        sys.exit("\nInterrompido. Rode de novo: o seed retoma de onde parou, sem duplicar.")
    with open("seed_output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print("\nResumo:", {k: len(v) for k, v in result.items()})
    print("IDs salvos em seed_output.json")
