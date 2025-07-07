telegram_token = 'ВАШ_ТОКЕН_TELEGRAM'  # Ваш токен Telegram Bot
support_account = 123456789  # Telegram ID аккаунта поддержки

yoomoney_token = 'ВАШ_YOOMONEY_TOKEN'
yoomoney_wallet = 'ВАШ_НОМЕР_КОШЕЛЬКА'

outline_api_url = "https://your.outline.server:12345/XXXXXXXXXXXX"
outline_cert_sha256 = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# Стоимость для создания новых ключей
PRICE_NEW = {
    5: 35,
    10: 55,
    25: 99,
    50: 145,
    998: 195,  # 1 месяц безлимит
    999: 555   # 3 месяца безлимит
}

# Стоимость для обновления ключей
PRICE_UPGRADE = {
    5: 35,
    10: 50,
    25: 90,
    50: 135,
    998: 175,  # 1 месяц безлимита
    999: 530   # 3 месяца безлимит
}

# Пути к файлам
USERS_FILE = 'data/users.txt'
USERS_USERNAME_FILE = 'data/users_username.txt'
KEYS_IDS_FILE = 'data/keys_ids.txt'
USERS_KEYS_EXPIRATIONS_FILE = 'data/users_keys_expirations.txt'
BANNED_USERS_FILE = 'data/banned_users.txt'
CHAT_LOG_FILE = 'data/chatlog.txt'
PROMOCODES_FILE = 'data/promocodes/promocodes.txt'
PROMO_ACTIVATION_LOGS = 'data/promocodes/activation_logs.txt'
PROMO_DISCOUNT_DIR = 'data/promocodes/discounts/'
TRANSACTION_LOGS_FILE = 'data/transaction_logs/buyers.txt'
UNLIMITED_BUYERS_LOGS = 'data/transaction_logs/unlimited_buyers.txt'
NOTIFIED_KEYS_FILE = 'data/notified_keys_ids.txt'

# Текста
START_MESSAGE_FILE = 'data/texts/start_message.txt'
SUPPORT_MESSAGE_FILE = 'data/texts/support_message.txt'
INFORMATION_FILE = 'data/texts/information.txt'
GUIDE_FILE = 'data/texts/guide.txt'

# Уведомления
NOTIFY_DEPLETED_FILE = 'data/notifications/key_depleted'
NOTIFY_EXPIRATION_FILE = 'data/notifications/key_expiration'
NOTIFY_EXPIRED_FILE = 'data/notifications/key_expired'
NOTIFY_LOW_TRAFFIC_FILE = 'data/notifications/low_traffic'
