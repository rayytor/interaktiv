# Rayyan Ekitap

*[English](README.en.md)*

Akıllı tahta için etkileşimli ders kitabı okuyucu. Millî Eğitim Bakanlığının
ortaöğretim ders kitaplarını (9–12. sınıf, 56 kitap) tahtada açar; her sayfadaki
etkinlikler önceden işaretlenmiştir, öğretmen bir etkinliğe dokununca etkinlik
tüm sınıfın okuyabileceği büyüklükte ekranı kaplar. Pardus ETAP tahtalarında
yönetici parolası gerekmeden, USB bellekten kurulur ve internet olmadan çalışır.

![Kitaplık](docs/images/library.jpg)

## Neler yapar

- **Kitaplık.** Kitaplar sınıfa göre gruplanır; son okunan kitap kaldığı
  sayfadan bir dokunuşla açılır. Kitaplar bir kez indirilir, sonra çevrimdışı
  kullanılır.
- **Okuma.** Çift sayfa, tek sayfa ve kaydırma görünümleri; sayfaya sığdırma,
  yakınlaştırma, döndürme; kitapta arama; sayfalar, içindekiler ve etkinlik
  listesi.
- **Etkinlikler.** Her etkinlik sayfa üzerinde işaretlidir. Dokunulduğunda
  odak moduna geçer: etkinlik ekranı kaplar, sorular tek tek gezilir. Yayıncının
  etkileşimli etkinlikleri (EBA) tarayıcıda açılır.
- **Tahta için.** Bütün denetimler ekranın altında, el mesafesindedir; sayfa
  çevirmek için ekranın kenarına dokunmak ya da sayfayı kaydırmak yeter. Ekran
  klavyesi gerektiren tek alan olan sayfa numarası için büyük tuşlu bir sayı
  tablası vardır. Dört okuma teması (koyu, açık, sepya, gece).

![Okuyucu](docs/images/reader.jpg)

![Odak modu](docs/images/focus.jpg)

## Tahtaya kurulum

Kurulum paketi bir USB bellekle gelir. Bellek tahtaya takılınca tahta
"Çalıştır" diye sorar; Rayyan Ekitap öğretmenin hesabına kurulur ve açılır.
Ayrıntılar: [docs/kurulum.md](docs/kurulum.md). Günlük kullanım:
[docs/kullanim.md](docs/kullanim.md).

Desteklenen sürümler: Pardus ETAP 23.4 ve 25 (Debian 12 ve 13). Kurulum
yalnızca oturum açan kullanıcı içindir ve `~/.local/share/interaktiv` altına
yapılır; sistem dosyalarına dokunulmaz.

## Kaynak koddan çalıştırma

Gerekenler: Python 3.11+, GTK 4.8+, libadwaita 1.2+, PyGObject, PyMuPDF.

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 xvfb   # Debian, Ubuntu, Pardus
pip install pymupdf psutil
./launch.sh
```

Testler: `python3 -m pytest`. Tahtanın kütüphane sürümleriyle test etmek için
`packaging/board/test-floor.sh` (Docker gerektirir). Geliştirici belgeleri
İngilizcedir: [docs/architecture.md](docs/architecture.md),
[docs/packaging.md](docs/packaging.md),
[docs/hotspot-pipeline.md](docs/hotspot-pipeline.md).

## Lisans ve içerik

Rayyan Ekitap, GNU Affero General Public License v3.0 ile dağıtılır
([LICENSE](LICENSE)); PDF görüntüleme için kullandığı PyMuPDF de AGPL
lisanslıdır. Ders kitapları Millî Eğitim Bakanlığının yayınıdır ve
OGM Materyal üzerinden indirilir; Rayyan Ekitap kitapların içeriğini değiştirmez
ve dağıtmaz. Arayüzde kullanılan Inter yazı tipi SIL Open Font License 1.1
ile lisanslıdır.
