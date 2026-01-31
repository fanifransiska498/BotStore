# Telegram Bot Jual Beli Produk

Script sederhana untuk bot Telegram yang membantu jual beli produk. Bot menyimpan
produk ke file JSON lokal. Hanya admin yang bisa menambah atau menghapus produk,
sementara pengguna lain bisa melihat daftar produk dan melakukan pembelian.
Bot sudah menggunakan tombol inline untuk mempermudah pemilihan produk dan
checkout.

## Persiapan

1. Buat bot melalui @BotFather dan ambil token.
2. Simpan token dan daftar admin ke environment variable:

```bash
export TELEGRAM_BOT_TOKEN="TOKEN_ANDA"
export TELEGRAM_ADMIN_IDS="123456789,987654321"
export PAYMENT_INSTRUCTIONS="Transfer ke BCA 123456 a/n Toko Anda"
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

Perintah pengguna:

- `/start` - pesan sambutan
- `/help` - daftar perintah
- `/list [kata_kunci]` - daftar produk
- `/detail <id>` - detail produk
- `/buy <id>` - pilih produk
- `/checkout <qty>` - checkout produk terpilih
- `/confirm <order_id>` - kirim screenshot pembayaran

Perintah admin:

- `/sell <nama> | <harga> | <stok> | <deskripsi> | <delivery>` - tambah produk
- `/sellform` - input produk via form/inline
- `/my` - produk milik admin
- `/remove <id>` - hapus produk

Contoh menambah produk:

```
/sell Kopi Arabica | 45000 | 10 | Kopi arabica 250gr | Kode voucher: ARB-2026
```

Contoh alur pembelian:

```
/buy 1
/checkout 2
/confirm 1001
```

Setelah menjalankan `/confirm`, kirim bukti pembayaran dalam bentuk screenshot
hasil transfer (foto). Admin akan menerima notifikasi dan dapat menerima atau
menolak pembayaran. Pembayaran akan otomatis ditolak jika lewat 1 menit tanpa
bukti.

## Penyimpanan Data

Produk disimpan di `data/products.json`. File dan folder akan dibuat otomatis
saat pertama kali bot dijalankan.
