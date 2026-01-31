import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DATA_PATH = Path(__file__).parent / "data" / "products.json"
STORE_LOCK = asyncio.Lock()


def _parse_admin_ids(raw: Optional[str]) -> Set[int]:
    if not raw:
        return set()
    ids: Set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            ids.add(int(part))
    return ids


ADMIN_IDS = _parse_admin_ids(os.environ.get("TELEGRAM_ADMIN_IDS"))


def _ensure_store_exists() -> None:
    if DATA_PATH.exists():
        return
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    initial = {"next_id": 1, "next_order_id": 1, "products": [], "orders": []}
    DATA_PATH.write_text(json.dumps(initial, indent=2), encoding="utf-8")


def _load_store() -> Dict[str, Any]:
    _ensure_store_exists()
    raw = DATA_PATH.read_text(encoding="utf-8")
    try:
        store = json.loads(raw)
    except json.JSONDecodeError:
        store = {"next_id": 1, "next_order_id": 1, "products": [], "orders": []}
    store.setdefault("next_id", 1)
    store.setdefault("next_order_id", 1)
    store.setdefault("products", [])
    store.setdefault("orders", [])
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


def _format_product_detail(
    product: Dict[str, Any],
    include_delivery: bool = False,
    include_seller: bool = False,
) -> str:
    seller = product.get("seller_username") or f"id {product['seller_id']}"
    lines = [
        f"ID: {product['id']}",
        f"Nama: {product['name']}",
        f"Harga: {_format_currency(product['price'])}",
        f"Stok: {product['stock']}",
        f"Penjual: {seller if include_seller else 'Admin'}",
        f"Deskripsi: {product['description']}",
    ]
    if include_delivery and product.get("delivery"):
        lines.append(f"Produk/Digital: {product['delivery']}")
    return "\n".join(lines)


def _get_product(store: Dict[str, Any], product_id: int) -> Optional[Dict[str, Any]]:
    return next(
        (item for item in store.get("products", []) if item["id"] == product_id),
        None,
    )


def _is_admin(user: Optional[User]) -> bool:
    if not user:
        return False
    return user.id in ADMIN_IDS


async def _require_admin(update: Update) -> bool:
    user = update.effective_user
    if _is_admin(user):
        return True
    await _reply(update, "Perintah ini hanya untuk admin.")
    return False


def _get_payment_instructions() -> str:
    instructions = os.environ.get("PAYMENT_INSTRUCTIONS", "").strip()
    if instructions:
        return instructions
    return "Instruksi pembayaran belum diset. Hubungi admin."


def _shorten(text: str, max_len: int = 40) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def _build_product_keyboard(
    products: List[Dict[str, Any]],
    limit: int = 10,
) -> Optional[InlineKeyboardMarkup]:
    rows: List[List[InlineKeyboardButton]] = []
    for product in products[:limit]:
        label = f"ID {product['id']} - {_shorten(product['name'], 32)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"select:{product['id']}")]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _build_checkout_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Checkout x1", callback_data=f"checkout:{product_id}:1"
                ),
                InlineKeyboardButton(
                    "Checkout x2", callback_data=f"checkout:{product_id}:2"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Checkout x5", callback_data=f"checkout:{product_id}:5"
                )
            ],
        ]
    )


def _build_confirm_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Kirim bukti pembayaran", callback_data=f"confirm:{order_id}"
                )
            ]
        ]
    )


def _build_admin_review_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Terima pembayaran", callback_data=f"admin:approve:{order_id}"
                ),
                InlineKeyboardButton(
                    "Tolak pembayaran", callback_data=f"admin:reject:{order_id}"
                ),
            ]
        ]
    )


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    if not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logging.warning("Gagal kirim notifikasi admin %s: %s", admin_id, exc)


async def _notify_admins_with_proof(
    context: ContextTypes.DEFAULT_TYPE,
    caption: str,
    photo_file_id: Optional[str] = None,
    document_file_id: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    if not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            if photo_file_id:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo_file_id,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            elif document_file_id:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=document_file_id,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=reply_markup,
                )
        except Exception as exc:
            logging.warning("Gagal kirim bukti ke admin %s: %s", admin_id, exc)


async def _reply(
    update: Update,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(text, reply_markup=reply_markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    await _reply(
        update,
        "Selamat datang di bot jual beli produk.\n"
        "Hanya admin yang dapat menambah atau menghapus produk.\n"
        "Ketik /help untuk melihat perintah.",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    await _reply(
        update,
        "Perintah pengguna:\n"
        "/list [kata_kunci] - daftar produk\n"
        "/detail <id> - detail produk\n"
        "/buy <id> - pilih produk\n"
        "/checkout <qty> - checkout produk terpilih\n"
        "/confirm <order_id> - kirim bukti pembayaran\n"
        "\nPerintah admin:\n"
        "/sell <nama> | <harga> | <stok> | <deskripsi> | <delivery>\n"
        "/my - daftar produk admin\n"
        "/remove <id> - hapus produk",
    )


async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip().lower()
    store = _load_store()
    all_products: List[Dict[str, Any]] = store.get("products", [])
    total_stock = sum(product.get("stock", 0) for product in all_products)
    products = all_products
    if query:
        products = [
            product
            for product in all_products
            if query in product["name"].lower()
            or query in product.get("description", "").lower()
        ]
    if not all_products:
        await _reply(update, "Belum ada produk yang tersedia.")
        return
    if query and not products:
        await _reply(
            update,
            f"Produk dengan kata kunci '{query}' tidak ditemukan.\n"
            f"Total produk: {len(all_products)} | Total stok: {total_stock}",
        )
        return
    lines = [
        "Daftar produk:",
        f"Total produk: {len(all_products)} | Total stok: {total_stock}",
    ]
    for product in products[:50]:
        lines.append(_format_product_summary(product))
    if len(products) > 50:
        lines.append("Menampilkan 50 produk pertama.")
    await _reply(update, "\n".join(lines), reply_markup=_build_product_keyboard(products))


async def detail_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Gunakan: /detail <id>")
        return
    product_id = _parse_int(context.args[0])
    if not product_id:
        await _reply(update, "ID produk tidak valid.")
        return
    store = _load_store()
    product = _get_product(store, product_id)
    if not product:
        await _reply(update, "Produk tidak ditemukan.")
        return
    user = update.effective_user
    await _reply(
        update,
        _format_product_detail(
            product,
            include_delivery=_is_admin(user),
            include_seller=_is_admin(user),
        ),
        reply_markup=_build_checkout_keyboard(product["id"]),
    )


async def sell_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update):
        return
    args = " ".join(context.args).strip()
    if not args:
        await _reply(
            update,
            "Gunakan: /sell <nama> | <harga> | <stok> | <deskripsi> | <delivery>",
        )
        return
    parts = [part.strip() for part in args.split("|")]
    if len(parts) < 3:
        await _reply(
            update,
            "Format salah. Gunakan: /sell <nama> | <harga> | <stok> | <deskripsi> | <delivery>",
        )
        return
    name = parts[0]
    price = _parse_int(parts[1])
    stock = _parse_int(parts[2])
    description = parts[3] if len(parts) >= 4 else "-"
    delivery = parts[4] if len(parts) >= 5 else ""
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
            "delivery": delivery,
            "seller_id": user.id,
            "seller_username": user.username or user.full_name,
            "created_at": now,
        }
        store["products"].append(product)
        _save_store(store)
    await _reply(
        update,
        "Produk ditambahkan:\n"
        + _format_product_detail(
            product,
            include_delivery=True,
            include_seller=True,
        ),
    )


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Gunakan: /buy <id>")
        return
    product_id = _parse_int(context.args[0])
    if not product_id:
        await _reply(update, "ID produk tidak valid.")
        return
    store = _load_store()
    product = _get_product(store, product_id)
    if not product:
        await _reply(update, "Produk tidak ditemukan.")
        return
    if product["stock"] <= 0:
        await _reply(update, "Stok produk ini habis.")
        return
    context.user_data["selected_product_id"] = product_id
    await _reply(
        update,
        "Produk dipilih:\n"
        + _format_product_detail(product, include_seller=False)
        + "\n\nGunakan /checkout <qty> untuk melanjutkan pembayaran.",
        reply_markup=_build_checkout_keyboard(product_id),
    )


def _order_summary_for_admin(order: Dict[str, Any]) -> str:
    return (
        "Order baru:\n"
        f"Order ID: {order['id']}\n"
        f"Produk: {order['product_name']}\n"
        f"Qty: {order['qty']}\n"
        f"Total: {_format_currency(order['total'])}\n"
        f"Pembeli: {order['buyer_name']} (id {order['buyer_id']})\n"
        f"Status: {order['status']}"
    )


def _order_summary_for_user(order: Dict[str, Any]) -> str:
    return (
        "Checkout berhasil dibuat.\n"
        f"Order ID: {order['id']}\n"
        f"Produk: {order['product_name']}\n"
        f"Jumlah: {order['qty']}\n"
        f"Total: {_format_currency(order['total'])}"
    )


async def _create_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product_id: int,
    qty: int,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    buyer = update.effective_user
    if not buyer:
        await _reply(update, "Pengguna tidak dikenal.")
        return None
    now = datetime.now(timezone.utc).isoformat()
    async with STORE_LOCK:
        store = _load_store()
        product = _get_product(store, product_id)
        if not product:
            await _reply(update, "Produk tidak ditemukan.")
            return None
        if product["stock"] < qty:
            await _reply(update, "Stok tidak cukup.")
            return None
        order_id = store["next_order_id"]
        store["next_order_id"] = order_id + 1
        order = {
            "id": order_id,
            "product_id": product["id"],
            "product_name": product["name"],
            "qty": qty,
            "total": product["price"] * qty,
            "buyer_id": buyer.id,
            "buyer_name": buyer.full_name,
            "status": "pending_payment",
            "created_at": now,
        }
        store["orders"].append(order)
        _save_store(store)
    await _notify_admins(context, _order_summary_for_admin(order))
    return order, product


async def _prompt_proof(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
) -> None:
    user = update.effective_user
    if not user:
        await _reply(update, "Pengguna tidak dikenal.")
        return
    async with STORE_LOCK:
        store = _load_store()
        order = next(
            (item for item in store.get("orders", []) if item["id"] == order_id),
            None,
        )
        if not order:
            await _reply(update, "Order tidak ditemukan.")
            return
        if order["buyer_id"] != user.id:
            await _reply(update, "Order ini bukan milik anda.")
            return
        if order["status"] not in {"pending_payment", "awaiting_proof"}:
            await _reply(update, "Order sudah diproses sebelumnya.")
            return
        order["status"] = "awaiting_proof"
        _save_store(store)
    context.user_data["awaiting_proof_order_id"] = order_id
    await _reply(
        update,
        "Silakan kirim bukti pembayaran (foto atau dokumen).",
    )


async def checkout_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Gunakan: /checkout <qty>")
        return
    selected_product_id = context.user_data.get("selected_product_id")
    if not selected_product_id:
        await _reply(update, "Pilih produk terlebih dahulu dengan /buy <id>.")
        return
    qty = _parse_int(context.args[0])
    if not qty or qty <= 0:
        await _reply(update, "Qty tidak valid.")
        return
    result = await _create_order(update, context, selected_product_id, qty)
    if not result:
        return
    order, _product = result
    await _reply(
        update,
        _order_summary_for_user(order)
        + "\n\n"
        f"Instruksi pembayaran:\n{_get_payment_instructions()}\n\n"
        f"Setelah pembayaran, ketik /confirm {order['id']}",
        reply_markup=_build_confirm_keyboard(order["id"]),
    )


async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Gunakan: /confirm <order_id>")
        return
    order_id = _parse_int(context.args[0])
    if not order_id:
        await _reply(update, "Order ID tidak valid.")
        return
    await _prompt_proof(update, context, order_id)


async def handle_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    order_id = context.user_data.get("awaiting_proof_order_id")
    if not order_id:
        await _reply(update, "Gunakan /confirm <order_id> sebelum mengirim bukti.")
        return
    message = update.effective_message
    if not message:
        return
    photo = message.photo[-1] if message.photo else None
    document = message.document
    if not photo and not document:
        await _reply(update, "Kirim bukti berupa foto atau dokumen.")
        return
    now = datetime.now(timezone.utc).isoformat()
    photo_id = photo.file_id if photo else None
    document_id = document.file_id if document else None
    async with STORE_LOCK:
        store = _load_store()
        order = next(
            (item for item in store.get("orders", []) if item["id"] == order_id),
            None,
        )
        if not order:
            await _reply(update, "Order tidak ditemukan.")
            return
        if order["buyer_id"] != user.id:
            await _reply(update, "Order ini bukan milik anda.")
            return
        if order["status"] not in {"pending_payment", "awaiting_proof"}:
            await _reply(update, "Order sudah diproses sebelumnya.")
            return
        order["status"] = "proof_submitted"
        order["proof_type"] = "photo" if photo_id else "document"
        order["proof_file_id"] = photo_id or document_id
        order["proof_submitted_at"] = now
        _save_store(store)
    context.user_data.pop("awaiting_proof_order_id", None)
    await _reply(update, "Bukti pembayaran diterima. Menunggu verifikasi admin.")
    caption = "Bukti pembayaran diterima.\n" + _order_summary_for_admin(order)
    await _notify_admins_with_proof(
        context,
        caption,
        photo_file_id=photo_id,
        document_file_id=document_id,
        reply_markup=_build_admin_review_keyboard(order["id"]),
    )


async def _approve_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
) -> str:
    admin = update.effective_user
    if not _is_admin(admin):
        return "Perintah ini hanya untuk admin."
    now = datetime.now(timezone.utc).isoformat()
    async with STORE_LOCK:
        store = _load_store()
        order = next(
            (item for item in store.get("orders", []) if item["id"] == order_id),
            None,
        )
        if not order:
            return "Order tidak ditemukan."
        if order["status"] != "proof_submitted":
            return "Order belum mengirim bukti pembayaran."
        product = _get_product(store, order["product_id"])
        if not product:
            order["status"] = "cancelled"
            order["cancelled_at"] = now
            _save_store(store)
            return "Produk sudah tidak tersedia. Order dibatalkan."
        if product["stock"] < order["qty"]:
            order["status"] = "cancelled"
            order["cancelled_at"] = now
            _save_store(store)
            return "Stok tidak cukup. Order dibatalkan."
        product["stock"] -= order["qty"]
        order["status"] = "paid"
        order["paid_at"] = now
        order["approved_by"] = admin.id if admin else None
        _save_store(store)
    delivery = product.get("delivery") or "Detail produk akan dikirim admin."
    try:
        await context.bot.send_message(
            chat_id=order["buyer_id"],
            text=(
                "Pembayaran disetujui. Berikut produk anda:\n"
                f"{delivery}\n\n"
                f"Produk: {order['product_name']}\n"
                f"Jumlah: {order['qty']}\n"
                f"Sisa stok: {product['stock']}"
            ),
        )
    except Exception as exc:
        logging.warning("Gagal kirim produk ke pembeli %s: %s", order["buyer_id"], exc)
    return "Pembayaran disetujui dan produk dikirim ke pembeli."


async def _reject_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
) -> str:
    admin = update.effective_user
    if not _is_admin(admin):
        return "Perintah ini hanya untuk admin."
    now = datetime.now(timezone.utc).isoformat()
    async with STORE_LOCK:
        store = _load_store()
        order = next(
            (item for item in store.get("orders", []) if item["id"] == order_id),
            None,
        )
        if not order:
            return "Order tidak ditemukan."
        if order["status"] not in {"proof_submitted", "pending_payment", "awaiting_proof"}:
            return "Order sudah diproses sebelumnya."
        order["status"] = "rejected"
        order["rejected_at"] = now
        order["rejected_by"] = admin.id if admin else None
        _save_store(store)
    try:
        await context.bot.send_message(
            chat_id=order["buyer_id"],
            text=(
                "Pembayaran anda ditolak. Silakan hubungi admin jika ada kesalahan.\n"
                f"Order ID: {order['id']}\n"
                f"Produk: {order['product_name']}\n"
                f"Total: {_format_currency(order['total'])}"
            ),
        )
    except Exception as exc:
        logging.warning("Gagal kirim penolakan ke pembeli %s: %s", order["buyer_id"], exc)
    return "Order ditolak dan pembeli sudah diberi notifikasi."


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    data = query.data
    if data.startswith("select:"):
        product_id = _parse_int(data.split(":", 1)[1] if ":" in data else "")
        if not product_id:
            await _reply(update, "ID produk tidak valid.")
            return
        store = _load_store()
        product = _get_product(store, product_id)
        if not product:
            await _reply(update, "Produk tidak ditemukan.")
            return
        context.user_data["selected_product_id"] = product_id
        await _reply(
            update,
            "Produk dipilih:\n"
            + _format_product_detail(product, include_seller=False)
            + "\n\nGunakan /checkout <qty> atau tombol di bawah.",
            reply_markup=_build_checkout_keyboard(product_id),
        )
        return
    if data.startswith("checkout:"):
        parts = data.split(":")
        if len(parts) < 3:
            await _reply(update, "Checkout tidak valid.")
            return
        product_id = _parse_int(parts[1])
        qty = _parse_int(parts[2])
        if not product_id or not qty:
            await _reply(update, "Checkout tidak valid.")
            return
        context.user_data["selected_product_id"] = product_id
        result = await _create_order(update, context, product_id, qty)
        if not result:
            return
        order, _product = result
        await _reply(
            update,
            _order_summary_for_user(order)
            + "\n\n"
            f"Instruksi pembayaran:\n{_get_payment_instructions()}\n\n"
            f"Setelah pembayaran, ketik /confirm {order['id']}",
            reply_markup=_build_confirm_keyboard(order["id"]),
        )
        return
    if data.startswith("confirm:"):
        order_id = _parse_int(data.split(":", 1)[1] if ":" in data else "")
        if not order_id:
            await _reply(update, "Order ID tidak valid.")
            return
        await _prompt_proof(update, context, order_id)
        return
    if data.startswith("admin:approve:"):
        order_id = _parse_int(data.split(":", 2)[2] if ":" in data else "")
        if not order_id:
            await _reply(update, "Order ID tidak valid.")
            return
        message = await _approve_order(update, context, order_id)
        await _reply(update, message)
        return
    if data.startswith("admin:reject:"):
        order_id = _parse_int(data.split(":", 2)[2] if ":" in data else "")
        if not order_id:
            await _reply(update, "Order ID tidak valid.")
            return
        message = await _reject_order(update, context, order_id)
        await _reply(update, message)
        return


async def my_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if not await _require_admin(update):
        return
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
    if not await _require_admin(update):
        return
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
    if not ADMIN_IDS:
        logging.warning("TELEGRAM_ADMIN_IDS belum diisi, perintah admin akan ditolak.")
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_products))
    application.add_handler(CommandHandler("detail", detail_product))
    application.add_handler(CommandHandler("sell", sell_product))
    application.add_handler(CommandHandler("buy", buy_product))
    application.add_handler(CommandHandler("checkout", checkout_product))
    application.add_handler(CommandHandler("confirm", confirm_payment))
    application.add_handler(CommandHandler("my", my_products))
    application.add_handler(CommandHandler("remove", remove_product))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(
            filters.PHOTO | (filters.Document.ALL & ~filters.COMMAND),
            handle_proof,
        )
    )

    application.run_polling()


if __name__ == "__main__":
    main()
