import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

DATA_PATH = Path(__file__).parent / "data" / "products.json"
STORE_LOCK = asyncio.Lock()


def _ensure_store_exists() -> None:
    if DATA_PATH.exists():
        return
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    initial = {"next_id": 1, "products": []}
    DATA_PATH.write_text(json.dumps(initial, indent=2), encoding="utf-8")


def _load_store() -> Dict[str, Any]:
    _ensure_store_exists()
    raw = DATA_PATH.read_text(encoding="utf-8")
    try:
        store = json.loads(raw)
    except json.JSONDecodeError:
        store = {"next_id": 1, "products": []}
    store.setdefault("next_id", 1)
    store.setdefault("products", [])
    return store


def _save_store(store: Dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _format_currency(value: int) -> str:
    return f"Rp {value:,}".replace(",", ".")


def _parse_int(value: str) -> Optional[int]:
    cleaned = value.replace(".", "").replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _format_product_summary(product: Dict[str, Any]) -> str:
    return (
        f"ID {product['id']}: {product['name']} - "
        f"{_format_currency(product['price'])} "
        f"(stok {product['stock']})"
    )


def _format_product_detail(product: Dict[str, Any]) -> str:
    seller = product.get("seller_username") or f"id {product['seller_id']}"
    return (
        f"ID: {product['id']}\n"
        f"Nama: {product['name']}\n"
        f"Harga: {_format_currency(product['price'])}\n"
        f"Stok: {product['stock']}\n"
        f"Penjual: {seller}\n"
        f"Deskripsi: {product['description']}"
    )


async def _reply(update: Update, text: str) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    await _reply(
        update,
        "Selamat datang di bot jual beli produk.\n"
        "Ketik /help untuk melihat perintah.",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    await _reply(
        update,
        "Perintah tersedia:\n"
        "/list [kata_kunci] - daftar produk\n"
        "/detail <id> - detail produk\n"
        "/sell <nama> | <harga> | <stok> | <deskripsi>\n"
        "/buy <id> <qty> - beli produk\n"
        "/my - produk milik anda\n"
        "/remove <id> - hapus produk anda",
    )


async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip().lower()
    store = _load_store()
    products: List[Dict[str, Any]] = store.get("products", [])
    if query:
        products = [
            product
            for product in products
            if query in product["name"].lower()
            or query in product.get("description", "").lower()
        ]
    if not products:
        await _reply(update, "Belum ada produk yang tersedia.")
        return
    lines = ["Daftar produk:"]
    for product in products[:50]:
        lines.append(_format_product_summary(product))
    if len(products) > 50:
        lines.append("Menampilkan 50 produk pertama.")
    await _reply(update, "\n".join(lines))


async def detail_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Gunakan: /detail <id>")
        return
    product_id = _parse_int(context.args[0])
    if not product_id:
        await _reply(update, "ID produk tidak valid.")
        return
    store = _load_store()
    product = next(
        (item for item in store.get("products", []) if item["id"] == product_id),
        None,
    )
    if not product:
        await _reply(update, "Produk tidak ditemukan.")
        return
    await _reply(update, _format_product_detail(product))


async def sell_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip()
    if not args:
        await _reply(
            update,
            "Gunakan: /sell <nama> | <harga> | <stok> | <deskripsi>",
        )
        return
    parts = [part.strip() for part in args.split("|")]
    if len(parts) < 3:
        await _reply(
            update,
            "Format salah. Gunakan: /sell <nama> | <harga> | <stok> | <deskripsi>",
        )
        return
    name = parts[0]
    price = _parse_int(parts[1])
    stock = _parse_int(parts[2])
    description = parts[3] if len(parts) >= 4 else "-"
    if not name:
        await _reply(update, "Nama produk tidak boleh kosong.")
        return
    if price is None or price <= 0:
        await _reply(update, "Harga tidak valid.")
        return
    if stock is None or stock <= 0:
        await _reply(update, "Stok tidak valid.")
        return
    user = update.effective_user
    if not user:
        await _reply(update, "Pengguna tidak dikenal.")
        return
    now = datetime.now(timezone.utc).isoformat()
    async with STORE_LOCK:
        store = _load_store()
        product_id = store["next_id"]
        store["next_id"] = product_id + 1
        product = {
            "id": product_id,
            "name": name,
            "price": price,
            "stock": stock,
            "description": description,
            "seller_id": user.id,
            "seller_username": user.username or user.full_name,
            "created_at": now,
        }
        store["products"].append(product)
        _save_store(store)
    await _reply(
        update,
        "Produk ditambahkan:\n" + _format_product_detail(product),
    )


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await _reply(update, "Gunakan: /buy <id> <qty>")
        return
    product_id = _parse_int(context.args[0])
    qty = _parse_int(context.args[1])
    if not product_id or not qty or qty <= 0:
        await _reply(update, "ID atau qty tidak valid.")
        return
    buyer = update.effective_user
    async with STORE_LOCK:
        store = _load_store()
        products = store.get("products", [])
        product = next(
            (item for item in products if item["id"] == product_id),
            None,
        )
        if not product:
            await _reply(update, "Produk tidak ditemukan.")
            return
        if product["stock"] < qty:
            await _reply(update, "Stok tidak cukup.")
            return
        product["stock"] -= qty
        _save_store(store)
    buyer_name = buyer.full_name if buyer else "pembeli"
    await _reply(
        update,
        "Pembelian berhasil.\n"
        f"Produk: {product['name']}\n"
        f"Jumlah: {qty}\n"
        f"Pembeli: {buyer_name}\n"
        f"Sisa stok: {product['stock']}",
    )


async def my_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    user = update.effective_user
    if not user:
        await _reply(update, "Pengguna tidak dikenal.")
        return
    store = _load_store()
    products = [
        item for item in store.get("products", []) if item["seller_id"] == user.id
    ]
    if not products:
        await _reply(update, "Anda belum memiliki produk.")
        return
    lines = ["Produk anda:"]
    for product in products:
        lines.append(_format_product_summary(product))
    await _reply(update, "\n".join(lines))


async def remove_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Gunakan: /remove <id>")
        return
    product_id = _parse_int(context.args[0])
    if not product_id:
        await _reply(update, "ID tidak valid.")
        return
    user = update.effective_user
    if not user:
        await _reply(update, "Pengguna tidak dikenal.")
        return
    async with STORE_LOCK:
        store = _load_store()
        products = store.get("products", [])
        product = next(
            (item for item in products if item["id"] == product_id),
            None,
        )
        if not product:
            await _reply(update, "Produk tidak ditemukan.")
            return
        if product["seller_id"] != user.id:
            await _reply(update, "Anda tidak boleh menghapus produk ini.")
            return
        store["products"] = [item for item in products if item["id"] != product_id]
        _save_store(store)
    await _reply(update, "Produk berhasil dihapus.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN terlebih dahulu.")
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_products))
    application.add_handler(CommandHandler("detail", detail_product))
    application.add_handler(CommandHandler("sell", sell_product))
    application.add_handler(CommandHandler("buy", buy_product))
    application.add_handler(CommandHandler("my", my_products))
    application.add_handler(CommandHandler("remove", remove_product))

    application.run_polling()


if __name__ == "__main__":
    main()
