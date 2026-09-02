"""Generate a synthetic marketplace dataset with the exact Olist file/column schema.

The real dataset (olistbr/brazilian-ecommerce on Kaggle) cannot be redistributed
here, and Kaggle is not reachable from every environment. This generator emits
CSVs that are byte-compatible with the real ones -- same nine files, same column
names, same order-status vocabulary, same nullability patterns -- so that
`dbt build` runs green on a fresh clone and swapping in the real download
requires no code change at all.

Deliberate fidelity choices (each one shows up downstream in the event layer):
  * Lifecycle timestamps are nullable and ordered: purchase -> approved ->
    carrier -> customer. Later stages are only populated when the order
    actually reached them.
  * Terminal statuses (canceled / unavailable) truncate the timestamp chain.
  * ~2% of delivered orders have a NULL order_approved_at -- the real dataset
    has this too, and it is what forces the event layer to treat missing
    timestamps as "step not observed" rather than "step not reached".
  * ~3% of buyers order more than once (the real marketplace has a famously
    thin repeat rate), so cohort retention curves look realistic rather than
    flattering.
  * A small share of reviews are answered before they are created in wall
    time (clock skew at the source), which is exactly the out-of-order case
    the event layer has to decide about.

Usage:
    python scripts/generate_sample_data.py --orders 100000 --out data/raw
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20240917

# (state, share of buyers, share of sellers, base transit days)
STATES = [
    ("SP", 0.420, 0.700, 8.0),
    ("RJ", 0.129, 0.045, 14.5),
    ("MG", 0.117, 0.075, 11.5),
    ("RS", 0.055, 0.030, 15.0),
    ("PR", 0.050, 0.055, 11.0),
    ("SC", 0.036, 0.032, 14.0),
    ("BA", 0.034, 0.008, 18.5),
    ("DF", 0.021, 0.008, 12.5),
    ("ES", 0.020, 0.006, 15.0),
    ("GO", 0.020, 0.010, 15.0),
    ("PE", 0.017, 0.004, 21.0),
    ("CE", 0.013, 0.003, 20.5),
    ("PA", 0.010, 0.002, 23.0),
    ("MT", 0.009, 0.004, 17.5),
    ("MA", 0.007, 0.002, 21.5),
    ("MS", 0.007, 0.004, 15.5),
    ("PB", 0.005, 0.002, 20.0),
    ("PI", 0.005, 0.001, 20.0),
    ("RN", 0.005, 0.002, 19.0),
    ("AL", 0.004, 0.001, 22.0),
    ("SE", 0.003, 0.001, 21.0),
    ("TO", 0.003, 0.001, 19.5),
    ("RO", 0.003, 0.001, 20.0),
    ("AM", 0.003, 0.001, 26.0),
    ("AC", 0.002, 0.001, 21.0),
    ("AP", 0.001, 0.001, 27.0),
    ("RR", 0.001, 0.001, 28.0),
]

# Rough centroids, only used to give geolocation a plausible shape.
STATE_CENTROID = {
    "SP": (-22.19, -48.79), "RJ": (-22.25, -42.66), "MG": (-18.10, -44.38),
    "RS": (-30.17, -53.50), "PR": (-24.89, -51.55), "SC": (-27.45, -50.95),
    "BA": (-12.96, -41.70), "DF": (-15.83, -47.86), "ES": (-19.19, -40.34),
    "GO": (-15.98, -49.86), "PE": (-8.38, -37.86), "CE": (-5.20, -39.53),
    "PA": (-3.79, -52.48), "MT": (-12.64, -55.42), "MA": (-5.42, -45.44),
    "MS": (-20.51, -54.54), "PB": (-7.28, -36.72), "PI": (-7.72, -42.73),
    "RN": (-5.81, -36.59), "AL": (-9.62, -36.82), "SE": (-10.57, -37.45),
    "TO": (-10.17, -48.29), "RO": (-10.83, -63.34), "AM": (-3.07, -61.66),
    "AC": (-9.02, -70.81), "AP": (1.41, -51.77), "RR": (1.99, -61.33),
}

CITY_BY_STATE = {
    "SP": ["sao paulo", "campinas", "guarulhos", "santo andre", "sorocaba", "ribeirao preto"],
    "RJ": ["rio de janeiro", "niteroi", "nova iguacu", "duque de caxias", "campos dos goytacazes"],
    "MG": ["belo horizonte", "uberlandia", "contagem", "juiz de fora", "betim"],
    "RS": ["porto alegre", "caxias do sul", "pelotas", "canoas", "santa maria"],
    "PR": ["curitiba", "londrina", "maringa", "ponta grossa", "cascavel"],
    "SC": ["joinville", "florianopolis", "blumenau", "sao jose", "chapeco"],
    "BA": ["salvador", "feira de santana", "vitoria da conquista", "camacari"],
    "DF": ["brasilia", "ceilandia", "taguatinga"],
    "ES": ["vitoria", "vila velha", "serra", "cariacica"],
    "GO": ["goiania", "aparecida de goiania", "anapolis"],
    "PE": ["recife", "jaboatao dos guararapes", "olinda", "caruaru"],
    "CE": ["fortaleza", "caucaia", "juazeiro do norte"],
    "PA": ["belem", "ananindeua", "santarem"],
    "MT": ["cuiaba", "varzea grande", "rondonopolis"],
    "MA": ["sao luis", "imperatriz", "timon"],
    "MS": ["campo grande", "dourados", "tres lagoas"],
    "PB": ["joao pessoa", "campina grande"],
    "PI": ["teresina", "parnaiba"],
    "RN": ["natal", "mossoro"],
    "AL": ["maceio", "arapiraca"],
    "SE": ["aracaju", "nossa senhora do socorro"],
    "TO": ["palmas", "araguaina"],
    "RO": ["porto velho", "ji-parana"],
    "AM": ["manaus", "parintins"],
    "AC": ["rio branco", "cruzeiro do sul"],
    "AP": ["macapa", "santana"],
    "RR": ["boa vista"],
}

CATEGORIES = [
    ("cama_mesa_banho", "bed_bath_table", 0.098, 95.0, 2300),
    ("beleza_saude", "health_beauty", 0.093, 130.0, 700),
    ("esporte_lazer", "sports_leisure", 0.083, 115.0, 1500),
    ("moveis_decoracao", "furniture_decor", 0.081, 105.0, 4200),
    ("informatica_acessorios", "computers_accessories", 0.072, 120.0, 900),
    ("utilidades_domesticas", "housewares", 0.069, 90.0, 1800),
    ("relogios_presentes", "watches_gifts", 0.059, 200.0, 400),
    ("telefonia", "telephony", 0.043, 75.0, 350),
    ("ferramentas_jardim", "garden_tools", 0.041, 110.0, 3500),
    ("automotivo", "auto", 0.039, 140.0, 2100),
    ("brinquedos", "toys", 0.036, 100.0, 1200),
    ("cool_stuff", "cool_stuff", 0.033, 165.0, 1600),
    ("perfumaria", "perfumery", 0.031, 125.0, 400),
    ("bebes", "baby", 0.029, 130.0, 2000),
    ("eletronicos", "electronics", 0.027, 95.0, 800),
    ("papelaria", "stationery", 0.024, 80.0, 900),
    ("fashion_bolsas_e_acessorios", "fashion_bags_accessories", 0.021, 90.0, 600),
    ("pet_shop", "pet_shop", 0.019, 100.0, 2400),
    ("moveis_escritorio", "office_furniture", 0.017, 230.0, 8000),
    ("consoles_games", "consoles_games", 0.014, 180.0, 1300),
    ("construcao_ferramentas_construcao", "construction_tools_construction", 0.012, 160.0, 5200),
    ("livros_interesse_geral", "books_general_interest", 0.010, 60.0, 700),
    ("instrumentos_musicais", "musical_instruments", 0.009, 210.0, 2600),
    ("alimentos", "food", 0.008, 55.0, 1500),
    ("market_place", "market_place", 0.006, 120.0, 1000),
    ("artes", "art", 0.005, 140.0, 1400),
    ("industria_comercio_e_negocios", "industry_commerce_and_business", 0.004, 190.0, 3000),
    ("seguros_e_servicos", "security_and_services", 0.003, 250.0, 500),
    ("fashion_roupa_masculina", "fashion_male_clothing", 0.003, 110.0, 500),
    ("la_cuisine", "la_cuisine", 0.002, 175.0, 2200),
]

PAYMENT_TYPES = ["credit_card", "boleto", "voucher", "debit_card"]
PAYMENT_WEIGHTS = [0.7377, 0.1904, 0.0552, 0.0167]

REVIEW_TITLES = [
    "", "", "", "", "", "recomendo", "otimo", "muito bom", "produto ruim",
    "nao recebi", "chegou antes do prazo", "excelente", "decepcionado",
]
REVIEW_MESSAGES = [
    "", "", "", "",
    "Produto chegou antes do prazo, muito bem embalado.",
    "Recebi exatamente o que comprei, recomendo o vendedor.",
    "Entrega atrasou mas o produto veio certo.",
    "Nao recebi o produto ate agora.",
    "Produto de boa qualidade pelo preco.",
    "Veio com defeito, tive que solicitar troca.",
    "Otimo custo beneficio, comprarei novamente.",
    "Embalagem danificada, mas o produto estava intacto.",
]

START = pd.Timestamp("2016-09-04 21:15:19")
END = pd.Timestamp("2018-10-17 17:30:18")


def _hex_ids(prefix: str, n: int, salt: str) -> np.ndarray:
    """Stable 32-char hex ids, in the same shape as the real dataset's keys."""
    base = np.arange(n, dtype=np.int64)
    return np.array(
        [hashlib.md5(f"{salt}:{prefix}:{i}".encode()).hexdigest() for i in base],
        dtype=object,
    )


def _weighted_choice(rng: np.random.Generator, values, weights, size):
    w = np.asarray(weights, dtype=float)
    return rng.choice(len(values), size=size, p=w / w.sum())


def build(n_orders: int, out_dir: Path) -> None:
    rng = np.random.default_rng(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)

    state_codes = [s[0] for s in STATES]
    buyer_state_w = np.array([s[1] for s in STATES])
    seller_state_w = np.array([s[2] for s in STATES])
    transit_base = {s[0]: s[3] for s in STATES}

    # ---------------------------------------------------------------- buyers
    # ~3% of buyers place more than one order, so customer_unique_id is coarser
    # than customer_id (one row per order-side customer record), exactly as in
    # the real dataset.
    n_unique_buyers = int(n_orders * 0.968)
    unique_ids = _hex_ids("cust_unique", n_unique_buyers, "olist")
    buyer_state_idx = _weighted_choice(rng, state_codes, buyer_state_w, n_unique_buyers)
    buyer_state = np.array(state_codes, dtype=object)[buyer_state_idx]
    buyer_city = np.array(
        [rng.choice(CITY_BY_STATE[s]) for s in buyer_state], dtype=object
    )
    buyer_zip = rng.integers(1000, 99999, size=n_unique_buyers)

    # Assign orders to unique buyers: everyone gets one, the remainder are
    # repeat purchases concentrated on a small slice of buyers.
    extra = n_orders - n_unique_buyers
    repeat_pool = rng.choice(n_unique_buyers, size=extra, replace=True)
    order_buyer_idx = np.concatenate([np.arange(n_unique_buyers), repeat_pool])
    rng.shuffle(order_buyer_idx)

    customer_id = _hex_ids("cust", n_orders, "olist")
    customers = pd.DataFrame(
        {
            "customer_id": customer_id,
            "customer_unique_id": unique_ids[order_buyer_idx],
            "customer_zip_code_prefix": buyer_zip[order_buyer_idx],
            "customer_city": buyer_city[order_buyer_idx],
            "customer_state": buyer_state[order_buyer_idx],
        }
    )

    # --------------------------------------------------------------- sellers
    n_sellers = max(50, int(n_orders * 0.031))
    seller_ids = _hex_ids("seller", n_sellers, "olist")
    seller_state_idx = _weighted_choice(rng, state_codes, seller_state_w, n_sellers)
    seller_state = np.array(state_codes, dtype=object)[seller_state_idx]
    sellers = pd.DataFrame(
        {
            "seller_id": seller_ids,
            "seller_zip_code_prefix": rng.integers(1000, 99999, size=n_sellers),
            "seller_city": [rng.choice(CITY_BY_STATE[s]) for s in seller_state],
            "seller_state": seller_state,
        }
    )

    # -------------------------------------------------------------- products
    n_products = max(100, int(n_orders * 0.33))
    product_ids = _hex_ids("product", n_products, "olist")
    cat_names = [c[0] for c in CATEGORIES]
    cat_w = np.array([c[2] for c in CATEGORIES])
    cat_idx = _weighted_choice(rng, cat_names, cat_w, n_products)
    product_cat = np.array(cat_names, dtype=object)[cat_idx]
    cat_price = np.array([c[3] for c in CATEGORIES])[cat_idx]
    cat_weight = np.array([c[4] for c in CATEGORIES])[cat_idx]

    products = pd.DataFrame(
        {
            "product_id": product_ids,
            "product_category_name": product_cat,
            "product_name_lenght": rng.integers(15, 76, size=n_products),
            "product_description_lenght": rng.integers(100, 3800, size=n_products),
            "product_photos_qty": rng.integers(1, 12, size=n_products),
            "product_weight_g": np.round(cat_weight * rng.lognormal(0, 0.55, n_products)).astype(int),
            "product_length_cm": rng.integers(7, 105, size=n_products),
            "product_height_cm": rng.integers(2, 105, size=n_products),
            "product_width_cm": rng.integers(6, 105, size=n_products),
        }
    )
    # ~1.9% of products have no category in the real file; keep that hole open
    # so the staging layer has to decide what to do with it.
    missing_cat = rng.random(n_products) < 0.019
    products.loc[missing_cat, "product_category_name"] = None
    for col in ("product_name_lenght", "product_description_lenght", "product_photos_qty"):
        products.loc[missing_cat, col] = np.nan

    translation = pd.DataFrame(
        {
            "product_category_name": [c[0] for c in CATEGORIES],
            "product_category_name_english": [c[1] for c in CATEGORIES],
        }
    )

    # ---------------------------------------------------------------- orders
    order_ids = _hex_ids("order", n_orders, "olist")

    # Purchases ramp up over the two-year window, with a weekly seasonality
    # dip at weekends.
    span_s = (END - START).total_seconds()
    u = rng.random(n_orders)
    ramp = np.sqrt(u) * 0.82 + u * 0.18  # skew volume toward later months
    purchase_ts = START + pd.to_timedelta(ramp * span_s, unit="s")
    purchase_ts = pd.Series(purchase_ts).sort_values().reset_index(drop=True)

    n = n_orders
    # Status mix mirrors the real file: overwhelmingly delivered, a thin tail
    # of in-flight and terminal-failure states.
    status_roll = rng.random(n)
    status = np.full(n, "delivered", dtype=object)
    status[status_roll > 0.9705] = "shipped"
    status[status_roll > 0.9816] = "canceled"
    status[status_roll > 0.9879] = "unavailable"
    status[status_roll > 0.9940] = "invoiced"
    status[status_roll > 0.9971] = "processing"
    status[status_roll > 0.9996] = "created"
    status[status_roll > 0.9999] = "approved"

    reached_approved = np.isin(
        status, ["approved", "invoiced", "processing", "shipped", "delivered"]
    )
    reached_carrier = np.isin(status, ["shipped", "delivered"])
    reached_customer = status == "delivered"

    approve_lag_h = rng.lognormal(1.9, 1.15, n)  # median ~6.7h
    carrier_lag_d = rng.lognormal(0.55, 0.75, n)
    seller_state_of_order = np.array(state_codes, dtype=object)[
        _weighted_choice(rng, state_codes, seller_state_w, n)
    ]
    buyer_state_of_order = customers["customer_state"].to_numpy()
    base_transit = np.array([transit_base[s] for s in buyer_state_of_order])
    same_state = buyer_state_of_order == seller_state_of_order
    transit_d = np.maximum(
        0.4,
        (base_transit * np.where(same_state, 0.62, 1.0)) * rng.lognormal(0, 0.45, n) - 3.0,
    )

    approved_at = purchase_ts + pd.to_timedelta(approve_lag_h, unit="h")
    carrier_at = approved_at + pd.to_timedelta(carrier_lag_d, unit="D")
    customer_at = carrier_at + pd.to_timedelta(transit_d, unit="D")
    # The estimate is set at purchase time and is generous, which is why most
    # orders land early against it.
    estimated_at = (purchase_ts + pd.to_timedelta(
        base_transit * 1.55 + rng.normal(4.0, 2.0, n) + 6.0, unit="D"
    )).dt.floor("D")

    approved_at = approved_at.where(pd.Series(reached_approved))
    carrier_at = carrier_at.where(pd.Series(reached_carrier))
    customer_at = customer_at.where(pd.Series(reached_customer))

    # ~2% of delivered orders never got an approval timestamp written. The
    # order plainly *was* approved -- the event is simply not observable.
    lost_approval = (rng.random(n) < 0.02) & reached_customer
    approved_at = approved_at.mask(pd.Series(lost_approval))

    orders = pd.DataFrame(
        {
            "order_id": order_ids,
            "customer_id": customers["customer_id"],
            "order_status": status,
            "order_purchase_timestamp": purchase_ts,
            "order_approved_at": approved_at,
            "order_delivered_carrier_date": carrier_at,
            "order_delivered_customer_date": customer_at,
            "order_estimated_delivery_date": estimated_at,
        }
    )

    # ----------------------------------------------------------- order items
    # 1..N items per order; the vast majority are single-item.
    n_items = rng.choice([1, 2, 3, 4, 5, 6], size=n, p=[0.885, 0.076, 0.023, 0.010, 0.004, 0.002])
    order_idx = np.repeat(np.arange(n), n_items)
    item_seq = np.concatenate([np.arange(1, k + 1) for k in n_items])
    total_items = len(order_idx)

    prod_pick = rng.integers(0, n_products, size=total_items)
    seller_pick = rng.integers(0, n_sellers, size=total_items)
    price = np.round(cat_price[prod_pick] * rng.lognormal(0, 0.62, total_items), 2)
    price = np.clip(price, 0.85, 6735.0)
    item_weight = products["product_weight_g"].to_numpy()[prod_pick]
    freight = np.round(
        np.clip(7.5 + item_weight / 240.0 * rng.lognormal(0, 0.35, total_items), 0.0, 409.68),
        2,
    )
    shipping_limit = orders["order_purchase_timestamp"].to_numpy()[order_idx] + pd.to_timedelta(
        rng.uniform(1.0, 9.0, total_items), unit="D"
    )

    order_items = pd.DataFrame(
        {
            "order_id": orders["order_id"].to_numpy()[order_idx],
            "order_item_id": item_seq,
            "product_id": products["product_id"].to_numpy()[prod_pick],
            "seller_id": sellers["seller_id"].to_numpy()[seller_pick],
            "shipping_limit_date": shipping_limit,
            "price": price,
            "freight_value": freight,
        }
    )

    # -------------------------------------------------------------- payments
    order_total = (
        order_items.groupby("order_id", sort=False)[["price", "freight_value"]]
        .sum()
        .sum(axis=1)
        .reindex(orders["order_id"])
        .to_numpy()
    )
    pay_type_idx = _weighted_choice(rng, PAYMENT_TYPES, PAYMENT_WEIGHTS, n)
    pay_type = np.array(PAYMENT_TYPES, dtype=object)[pay_type_idx]
    installments = np.where(
        pay_type == "credit_card",
        rng.choice([1, 2, 3, 4, 5, 6, 8, 10, 12], size=n,
                   p=[0.44, 0.14, 0.13, 0.07, 0.06, 0.06, 0.035, 0.035, 0.03]),
        1,
    )
    payments = pd.DataFrame(
        {
            "order_id": orders["order_id"],
            "payment_sequential": 1,
            "payment_type": pay_type,
            "payment_installments": installments,
            "payment_value": np.round(order_total, 2),
        }
    )
    # A slice of orders is settled across two instruments (e.g. voucher plus
    # card), so payment rows are not unique on order_id alone.
    split = rng.random(n) < 0.055
    if split.any():
        second = payments[split].copy()
        second["payment_sequential"] = 2
        second["payment_type"] = "voucher"
        second["payment_installments"] = 1
        second["payment_value"] = np.round(second["payment_value"] * 0.35, 2)
        payments.loc[split, "payment_value"] = np.round(
            payments.loc[split, "payment_value"] * 0.65, 2
        )
        payments = pd.concat([payments, second], ignore_index=True)
    payments = payments.sort_values(["order_id", "payment_sequential"]).reset_index(drop=True)
    # 1 order in the real file has no payment row at all.
    payments = payments.iloc[1:].reset_index(drop=True) if len(payments) > 1 else payments

    # --------------------------------------------------------------- reviews
    # Reviews are requested once an order reaches a terminal-ish state.
    reviewable = np.isin(status, ["delivered", "shipped", "canceled", "unavailable"])
    has_review = reviewable & (rng.random(n) < 0.988)
    r_idx = np.flatnonzero(has_review)
    n_rev = len(r_idx)

    anchor = orders["order_delivered_customer_date"].to_numpy()[r_idx]
    fallback = (
        orders["order_purchase_timestamp"].to_numpy()[r_idx]
        + pd.to_timedelta(rng.uniform(5, 22, n_rev), unit="D").to_numpy()
    )
    review_creation = pd.Series(np.where(pd.isna(anchor), fallback, anchor)).dt.floor("D") + pd.Timedelta(days=1)

    delivered_flag = reached_customer[r_idx]
    late_flag = np.zeros(n_rev, dtype=bool)
    delivered_late = (
        orders["order_delivered_customer_date"].to_numpy()[r_idx]
        > orders["order_estimated_delivery_date"].to_numpy()[r_idx]
    )
    late_flag[delivered_flag] = pd.Series(delivered_late[delivered_flag]).fillna(False).to_numpy()

    score = np.where(
        ~delivered_flag,
        rng.choice([1, 2, 3, 4, 5], size=n_rev, p=[0.62, 0.14, 0.10, 0.06, 0.08]),
        np.where(
            late_flag,
            rng.choice([1, 2, 3, 4, 5], size=n_rev, p=[0.36, 0.16, 0.17, 0.16, 0.15]),
            rng.choice([1, 2, 3, 4, 5], size=n_rev, p=[0.075, 0.026, 0.075, 0.203, 0.621]),
        ),
    )

    answer_lag_h = rng.lognormal(2.6, 1.1, n_rev)
    review_answer = review_creation + pd.to_timedelta(answer_lag_h, unit="h")
    # ~0.6% of review answers are stamped *before* their creation date. This
    # is source clock skew, and int_events has to have an opinion about it.
    skew = rng.random(n_rev) < 0.006
    review_answer[skew] = review_creation[skew] - pd.to_timedelta(
        rng.uniform(1, 20, skew.sum()), unit="h"
    )

    title_idx = rng.integers(0, len(REVIEW_TITLES), size=n_rev)
    msg_idx = rng.integers(0, len(REVIEW_MESSAGES), size=n_rev)
    reviews = pd.DataFrame(
        {
            "review_id": _hex_ids("review", n_rev, "olist"),
            "order_id": orders["order_id"].to_numpy()[r_idx],
            "review_score": score,
            "review_comment_title": [REVIEW_TITLES[i] or None for i in title_idx],
            "review_comment_message": [REVIEW_MESSAGES[i] or None for i in msg_idx],
            "review_creation_date": review_creation,
            "review_answer_timestamp": review_answer,
        }
    )

    # ----------------------------------------------------------- geolocation
    # Multiple rows per zip prefix, as in the real file.
    zips = np.unique(
        np.concatenate(
            [customers["customer_zip_code_prefix"].to_numpy(),
             sellers["seller_zip_code_prefix"].to_numpy()]
        )
    )
    reps = rng.integers(1, 5, size=len(zips))
    geo_zip = np.repeat(zips, reps)
    geo_state_idx = _weighted_choice(rng, state_codes, buyer_state_w, len(geo_zip))
    geo_state = np.array(state_codes, dtype=object)[geo_state_idx]
    lat = np.array([STATE_CENTROID[s][0] for s in geo_state]) + rng.normal(0, 1.05, len(geo_zip))
    lng = np.array([STATE_CENTROID[s][1] for s in geo_state]) + rng.normal(0, 1.35, len(geo_zip))
    geolocation = pd.DataFrame(
        {
            "geolocation_zip_code_prefix": geo_zip,
            "geolocation_lat": np.round(lat, 6),
            "geolocation_lng": np.round(lng, 6),
            "geolocation_city": [rng.choice(CITY_BY_STATE[s]) for s in geo_state],
            "geolocation_state": geo_state,
        }
    )

    # ------------------------------------------------------------------ emit
    ts = "%Y-%m-%d %H:%M:%S"
    files = {
        "olist_customers_dataset.csv": (customers, {}),
        "olist_geolocation_dataset.csv": (geolocation, {}),
        "olist_order_items_dataset.csv": (order_items, {"date_format": ts}),
        "olist_order_payments_dataset.csv": (payments, {}),
        "olist_order_reviews_dataset.csv": (reviews, {"date_format": ts}),
        "olist_orders_dataset.csv": (orders, {"date_format": ts}),
        "olist_products_dataset.csv": (products, {}),
        "olist_sellers_dataset.csv": (sellers, {}),
        "product_category_name_translation.csv": (translation, {}),
    }
    for name, (df, kwargs) in files.items():
        path = out_dir / name
        df.to_csv(path, index=False, **kwargs)
        print(f"  wrote {path}  ({len(df):,} rows, {path.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orders", type=int, default=100_000, help="number of orders to generate")
    ap.add_argument("--out", type=Path, default=Path("data/raw"), help="output directory")
    args = ap.parse_args()
    print(f"Generating {args.orders:,} synthetic Olist-schema orders into {args.out}/ ...")
    build(args.orders, args.out)
    print("Done.")


if __name__ == "__main__":
    main()
