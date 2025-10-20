import asyncio
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from bot.config import BOT_TOKEN, WEBAPP_BASE_URL
from backend.database import SessionLocal
from backend.models import Product, Order


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Export BOT_TOKEN env var.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start_cmd(message: types.Message):
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть магазин", web_app=WebAppInfo(url=f"{WEBAPP_BASE_URL}/index.html"))],
            [InlineKeyboardButton(text="Панель администратора", web_app=WebAppInfo(url=f"{WEBAPP_BASE_URL}/admin.html"))],
        ])
        await message.answer("Добро пожаловать в Candy Store!", reply_markup=markup)

    @dp.message(F.web_app_data)
    async def handle_web_app_data(message: types.Message):
        try:
            raw = message.web_app_data.data
            # Accept single id ("5"), comma-separated ("1,2,3"), or JSON ("[1,2]")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                if "," in raw:
                    data = [int(x) for x in raw.split(",") if x.strip().isdigit()]
                else:
                    data = [int(raw)] if raw.strip().isdigit() else []

            product_ids = [int(x) for x in data]
            if not product_ids:
                await message.answer("Не удалось распознать товары заказа.")
                return

            with SessionLocal() as db:
                products = db.query(Product).filter(Product.id.in_(product_ids)).all()
                if not products:
                    await message.answer("Товары не найдены.")
                    return
                total = sum(p.price for p in products)
                order = Order(
                    user_id=message.from_user.id,
                    items=json.dumps(product_ids, ensure_ascii=False),
                    total=total,
                    status="pending",
                )
                db.add(order)
                db.commit()
                db.refresh(order)

            names = ", ".join(p.name for p in products)
            await message.answer(f"Заказ №{order.id} оформлен: {names}. Сумма: {total:.2f} ₽")
        except Exception:
            await message.answer("Произошла ошибка при оформлении заказа.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

