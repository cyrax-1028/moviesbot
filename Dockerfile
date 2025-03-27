# Python bazasini ishlatish
FROM python:3.12

# Ishchi katalogni yaratish
WORKDIR /app

# Kerakli kutubxonalarni o‘rnatish
RUN apt-get update && apt-get install -y libpq-dev

# Kodni konteynerga ko‘chirish
COPY . .

# Virtual muhit yaratish
RUN python -m venv /opt/venv

# Virtual muhitni aktivlashtirish
ENV PATH="/opt/venv/bin:$PATH"

# Kutubxonalarni o‘rnatish
RUN pip install --no-cache-dir -r requirements.txt

# Asosiy faylni ishga tushirish
CMD ["python", "main.py"]
