# Telegram Bot Jual Beli Produk

Script sederhana untuk bot Telegram yang membantu jual beli produk. Bot menyimpan
produk ke file JSON lokal dan menyediakan perintah dasar untuk menjual, membeli,
dan melihat daftar produk.

## Persiapan

1. Buat bot melalui @BotFather dan ambil token.
2. Simpan token ke environment variable:

```bash
export TELEGRAM_BOT_TOKEN="TOKEN_ANDA"
```

## Instalasi

```bash
python -m pip install -r requirements.txt
```

## Menjalankan Bot

```bash
python bot.py
```

## Perintah

- `/start` - pesan sambutan
- `/help` - daftar perintah
- `/list [kata_kunci]` - daftar produk
- `/detail <id>` - detail produk
- `/sell <nama> | <harga> | <stok> | <deskripsi>` - tambah produk
- `/buy <id> <qty>` - beli produk
- `/my` - produk milik anda
- `/remove <id>` - hapus produk anda

Contoh menambah produk:

```
/sell Kopi Arabica | 45000 | 10 | Kopi arabica 250gr
```

## Penyimpanan Data

Produk disimpan di `data/products.json`. File dan folder akan dibuat otomatis
saat pertama kali bot dijalankan.
