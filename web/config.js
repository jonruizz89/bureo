// Configuración pública de Limón. La clave publicable solo permite lo que dejan
// las políticas de seguridad por filas: leer el catálogo y gestionar los datos propios.
window.BUREO_CONFIG = {
  supabaseUrl: 'https://pxeqbxxfsrvsxcdfiddc.supabase.co',
  supabaseKey: 'sb_publishable_1oeCUB3vf4XFVH-BS_Km5w_V6NgQgUY',
  city: 'madrid',
  google: true,
  // Cloudflare Turnstile: clave PÚBLICA del widget (la secreta va solo en Supabase → Auth → Bot protection). Vacía = sin captcha.
  turnstile: '0x4AAAAAAFfGPQYgv8dEk1md'
};
