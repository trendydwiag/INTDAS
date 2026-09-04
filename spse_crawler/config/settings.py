"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InstansiConfig(BaseSettings):
    """A single instansi / sub-site target."""

    kode: str = Field(
        ...,
        description="Short URL code used in SPSE paths, e.g. 'kemendagri'.",
    )
    nama: str = Field(
        default="",
        description="Human-readable instansi name.",
    )
    base_url: str = Field(
        default="https://spse.inaproc.id",
        description="Base URL of the SPSE instance.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this instansi is active for crawling.",
    )

    @property
    def host_url(self) -> str:
        """Full host URL for this instansi."""
        return f"{self.base_url}/{self.kode}"

    def dt_endpoint(self, endpoint: str, tahun: int) -> str:
        """DataTables POST endpoint URL."""
        return f"{self.base_url}/{self.kode}/dt/{endpoint}?tahun={tahun}"

    def detail_url(self, id_lelang: str) -> str:
        """Full URL to the pengumuman detail page."""
        return f"{self.base_url}/{self.kode}/lelang/{id_lelang}/pengumumanlelang"

    def main_page_url(self, section: str = "lelang", tahun: int = 2026) -> str:
        """Main listing page URL to obtain CSRF token."""
        return f"{self.base_url}/{self.kode}/{section}?tahun={tahun}"


# ---------------------------------------------------------------------------
# All 736 production instansi codes from SPSE portal
# ---------------------------------------------------------------------------
INSTANSI_CODES: list[str] = [
    "acehbaratdayakab", "acehbaratkab", "acehbesarkab", "acehjayakab",
    "acehprov", "acehselatankab", "acehsingkilkab", "acehtamiangkab",
    "acehtengahkab", "acehtenggarakab", "acehtimurkab", "acehutara",
    "agamkab", "alorkab", "ambon", "anambaskab", "asahankab", "asmatkab",
    "atrbpn", "babelprov", "badanpangan", "badungkab", "bakamla",
    "balangankab", "balikpapan", "baliprov", "bandaacehkota",
    "bandarlampungkota", "bandung", "bandungbaratkab", "bandungkab",
    "banggaikab", "banggaikep", "banggailautkab", "bangka",
    "bangkabaratkab", "bangkalankab", "bangkaselatankab", "bangkatengahkab",
    "banglikab", "banjarbarukota", "banjarkab", "banjarkota",
    "banjarmasinkota", "banjarnegarakab", "bantaengkab", "bantenprov",
    "bantulkab", "banyuasinkab", "banyumaskab", "banyuwangikab",
    "bappenas", "baritokualakab", "baritoselatankab", "baritotimurkab",
    "baritoutarakab", "barrukab", "basarnas", "batam", "batangharikab",
    "batangkab", "batubarakab", "batukota", "baubaukota", "bekasikab",
    "bekasikota", "belitung", "beltim", "belukab", "benermeriahkab",
    "bengkaliskab", "bengkayangkab", "bengkulukota", "bengkuluprov",
    "bengkuluselatankab", "bengkulutengahkab", "bengkuluutarakab",
    "beraukab", "biakkab", "big", "bimakab", "bimakota", "bin",
    "binjaikota", "bintankab", "bireuenkab", "bitungkota", "bkn", "bkpm",
    "blitarkab", "blitarkota", "blorakab", "bmkg", "bnn", "bnpb", "bnpp",
    "bnpt", "boalemokab", "bogorkab", "bojonegorokab", "bolmongkab",
    "bolmutkab", "bolselkab", "boltimkab", "bombanakab", "bondowosokab",
    "bone", "bonebolangokab", "bontangkota", "bovendigoelkab", "boyolali",
    "bpbatam", "bpkp", "bps", "brebeskab", "brin", "bssn",
    "bukittinggikota", "bulelengkab", "bulukumbakab", "bulungan",
    "bungokab", "buolkab", "burselkab", "burukab", "butonkab",
    "butonselatankab", "butontengahkab", "butonutarakab", "ciamiskab",
    "cianjurkab", "cilacapkab", "cilegon", "cimahikota", "cirebonkab",
    "dairikab", "deiyaikab", "deliserdangkab", "demakkab", "denpasarkota",
    "dephub", "depok", "dharmasrayakab", "dogiyaikab", "dompukab",
    "donggala", "dpd", "dpr", "dumaikota", "empatlawangkab", "endekab",
    "enrekangkab", "esdm", "fakfakkab", "florestimurkab", "gayolueskab",
    "gianyarkab", "gorontalokab", "gorontalokota", "gorontaloprov",
    "gorutkab", "gowakab", "gresikkab", "grobogan", "gunungkidulkab",
    "gunungmaskab", "gunungsitolikota", "halbarkab", "halmaheraselatankab",
    "halmaherautarakab", "haltengkab", "haltimkab", "hstkab", "hsu",
    "hulusungaiselatankab", "humbanghasundutankab", "indramayukab",
    "inhilkab", "inhukab", "intanjayakab", "jabarprov", "jakarta",
    "jambikota", "jambiprov", "jatengprov", "jatimprov", "jayapurakab",
    "jayapurakota", "jayawijayakab", "jemberkab", "jembranakab",
    "jenepontokab", "jepara", "jogjakota", "jogjaprov", "jombangkab",
    "kaboki", "kaimanakab", "kalbarprov", "kalselprov", "kaltaraprov",
    "kalteng", "kaltimprov", "kamparkab", "kapuashulukab", "kapuaskab",
    "karanganyarkab", "karangasemkab", "karantinaindonesia", "karawangkab",
    "karimunkab", "karokab", "katingankab", "kaurkab", "kayongutarakab",
    "kebumenkab", "kedirikab", "kedirikota", "keeromkab", "kehutanan",
    "kejaksaan", "kemenag", "kemendag", "kemendagri", "kemendesa",
    "kemendikdasmen", "kemendukbangga", "kemenkeu", "kemenkopukm",
    "kemenkum", "kemenpar", "kemenperin", "kemenpora", "kemensos",
    "kemhan", "kemkes", "kemlu", "kemnaker", "kendalkab", "kendarikota",
    "kepahiangkab", "kepriprov", "kepulauanarukab", "kepulauanselayarkab",
    "kepulauansulakab", "kepyapenkab", "kerincikab", "ketapangkab", "kkp",
    "klaten", "klungkungkab", "kolakakab", "kolakatimurkab", "kolutkab",
    "komdigi", "konawekab", "konaweselatankab", "konaweutarakab",
    "konkepkab", "kotabarukab", "kotabogor", "kotamobagu", "kotaprabumulih",
    "kotawaringinbaratkab", "kotimkab", "kp2mi", "kpu", "kuansing",
    "kuburayakab", "kuduskab", "kukarkab", "kulonprogokab", "kuningankab",
    "kupangkab", "kupangkota", "kutaibaratkab", "kutaitimurkab",
    "labuhanbatukab", "labuhanbatuselatankab", "labura", "lahatkab",
    "lamandaukab", "lamongankab", "lampungbaratkab", "lampungprov",
    "lampungtengahkab", "lampungutarakab", "landakkab", "langkatkab",
    "langsakota", "lannyjayakab", "lebakkab", "lebongkab", "lembatakab",
    "lemhannas", "lhokseumawekota", "limapuluhkotakab", "linggakab",
    "lkpp", "lombokbaratkab", "lomboktengahkab", "lomboktimurkab",
    "lombokutarakab", "lubuklinggaukota", "lumajangkab", "luwukab",
    "luwutimurkab", "luwuutarakab", "mabestni", "madina", "madiunkab",
    "madiunkota", "magelangkab", "magelangkota", "magetan",
    "mahakamulukab", "mahkamahagung", "majalengkakab", "majenekab",
    "makassar", "malakakab", "malangkab", "malangkota", "malinau",
    "maltengkab", "malukubaratdayakab", "malukuprov",
    "malukutenggarakab", "malutprov", "mamasakab", "mamberamorayakab",
    "mamberamotengahkab", "mamujukab", "mamujutengahkab", "manadokota",
    "manggaraibaratkab", "manggaraikab", "manggaraitimurkab",
    "manokwarikab", "manselkab", "mappikab", "maroskab", "mataramkota",
    "melawikab", "mempawahkab", "menpan", "mentawaikab", "meranginkab",
    "merantikab", "merauke", "mesujikab", "metrokota", "mimikakab",
    "minahasa", "minselkab", "minut", "mitrakab", "mkri", "mojokertokab",
    "mojokertokota", "morowalikab", "morowaliutarakab", "mpr",
    "muaraenimkab", "muarojambikab", "mubakab", "mukomukokab",
    "munabaratkab", "munakab", "muratarakab", "murungrayakab",
    "musirawaskab", "nabirekab", "naganrayakab", "nagekeokab", "nasional",
    "natunakab", "ndugakab", "ngadakab", "nganjukkab", "ngawikab",
    "niasbaratkab", "niaskab", "niasselatankab", "niasutarakab",
    "ntbprov", "nttprov", "nunukankab", "oganilirkab", "okukab",
    "okuselatankab", "okutimurkab", "pacitankab", "padang",
    "padanglawaskab", "padanglawasutarakab", "padangpanjang",
    "padangpariamankab", "padangsidimpuankota", "pagaralamkota",
    "pakpakbharatkab", "palangkaraya", "palembang", "palikab",
    "palopokota", "palukota", "pamekasankab", "pandeglangkab",
    "pangkalpinangkota", "pangkepkab", "paniaikab", "papua",
    "papuabaratprov", "pareparekota", "pariamankota", "parigimoutongkab",
    "pasamanbaratkab", "pasamankab", "pasangkayukab", "paserkab",
    "pasuruankab", "pasuruankota", "patikab", "payakumbuhkota",
    "pegafkab", "pegbintangkab", "pekalongankab", "pekalongankota",
    "pekanbaru", "pelalawankab", "pemalangkab", "pematangsiantar",
    "pemkomedan", "penajamkab", "pertanian", "pesawarankab",
    "pesisirbaratkab", "pesisirselatankab", "pidiejayakab", "pidiekab",
    "pinrangkab", "pohuwatokab", "polkam", "polmankab", "polri", "pom",
    "ponorogo", "pontianak", "posokab", "pringsewukab", "probolinggokab",
    "probolinggokota", "pu", "pulangpisaukab", "pulaumorotaikab",
    "puncakjayakab", "puncakkab", "purbalinggakab", "purwakartakab",
    "purworejokab", "rajaampatkab", "rejanglebongkab", "rembangkab",
    "riau", "rohilkab", "rokanhulukab", "rotendaokab", "sabangkota",
    "saburaijuakab", "salatiga", "samarindakota", "sambas", "samosirkab",
    "sampangkab", "sanggau", "sangihekab", "sarmikab", "sarolangunkab",
    "sawahluntokota", "sbbkab", "sbdkab", "sekadaukab", "selumakab",
    "semarangkab", "semarangkota", "serambagiantimurkab", "serangkab",
    "serangkota", "serdangbedagaikab", "seruyankab", "siakkab",
    "sibolgakota", "sidoarjokab", "sidrapkab", "sigikab", "sijunjung",
    "sikkakab", "simalungunkab", "simeuluekab", "singkawangkota",
    "sinjaikab", "sintang", "sitarokab", "situbondokab", "slemankab",
    "solokkab", "solokkota", "solselkab", "soppeng", "sorongkab",
    "sorongkota", "sorongselatankab", "sragenkab", "subang",
    "subulussalamkota", "sukabumikab", "sukamarakab", "sukoharjokab",
    "sulbarprov", "sulselprov", "sultengprov", "sultraprov", "sulutprov",
    "sumbabaratkab", "sumbarprov", "sumbatengahkab", "sumbatimurkab",
    "sumbawabaratkab", "sumbawakab", "sumedangkab", "sumenepkab",
    "sumselprov", "sumutprov", "sungaipenuhkota", "supiorikab",
    "surabaya", "surakarta", "tabalongkab", "tabanankab", "takalarkab",
    "talaudkab", "taliabukab", "tambrauwkab", "tanahbumbukab",
    "tanahdatar", "tanahlautkab", "tanatidungkab", "tanatorajakab",
    "tangerangkab", "tangerangkota", "tangerangselatankota", "tanggamus",
    "tanimbar", "tanjabbarkab", "tanjabtimkab", "tanjungbalaikota",
    "tanjungpinangkota", "tapinkab", "tapselkab", "tapteng", "taputkab",
    "tarakankota", "tasikmalayakab", "tasikmalayakota",
    "tebingtinggikota", "tebokab", "tegalkab", "tegalkota",
    "telukbintunikab", "temanggungkab", "ternatekota", "tidorekota",
    "tni-au", "tnial", "tobakab", "tojounauna", "tolikarakab",
    "tolitolikab", "tomohon", "torajautarakab", "trenggalekkab", "ttskab",
    "ttukab", "tualkota", "tubaba", "tubankab", "tulungagung", "tvri",
    "ui", "unand", "undip", "usu", "wajokab", "wakatobikab", "wantannas",
    "waykanankab", "wondamakab", "wonogirikab", "wonosobokab",
    "yahukimokab",
]

# Default subset for quick testing
DEFAULT_INSTANSI_CODES: list[str] = [
    "kemendagri", "jakarta", "jabarprov", "jatengprov", "jatimprov",
    "kemenkeu", "kemkes", "kemendag", "nasional", "pertanian",
]


class Settings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_prefix="SPSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- crawler behaviour ----------------------------------------------------
    max_concurrent_workers: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum concurrent browser contexts / HTTP workers.",
    )
    request_timeout: float = Field(
        default=30.0,
        gt=0,
        description="Per-request timeout in seconds.",
    )
    retry_max_attempts: int = Field(
        default=3,
        ge=0,
        description="Maximum retry attempts on transient errors.",
    )
    retry_backoff_base: float = Field(
        default=2.0,
        gt=0,
        description="Base multiplier for exponential backoff.",
    )
    rate_limit_delay: float = Field(
        default=0.6,
        ge=0,
        description="Seconds to wait between requests to the same host (rate limiting).",
    )
    tahun_anggaran: int = Field(
        default=2026,
        description="Budget year to scrape.",
    )
    page_size: int = Field(
        default=300,
        ge=1,
        le=500,
        description="DataTables page size (max 300 per SPSE server).",
    )

    # -- intelligence pipeline -------------------------------------------------
    ai_pipeline_batch_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Max jobs processed per pipeline execution cycle.",
    )
    ai_pipeline_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max attempts for a transient-failing intelligence job.",
    )
    ai_pipeline_retry_delay_minutes: int = Field(
        default=15,
        ge=0,
        description="Base delay (minutes) before retrying a transient failure.",
    )
    ai_pipeline_stale_minutes: int = Field(
        default=30,
        ge=1,
        description="Jobs stuck in PROCESSING longer than this are returned to PENDING.",
    )
    ai_max_matches_per_day: int = Field(
        default=100,
        ge=1,
        description="Daily AI Match execution budget per company.",
    )

    # -- stealth / browser ----------------------------------------------------
    headless: bool = Field(
        default=True,
        description="Run Playwright browser in headless mode.",
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        description="Default User-Agent header for browser requests.",
    )

    # -- HTTP default headers -------------------------------------------------
    # NOTE: Do NOT include X-Requested-With here — it causes SPSE to return
    # non-HTML responses when fetching the CSRF token page.
    # Do NOT include Accept-Encoding — let httpx handle decompression automatically.
    default_headers: dict[str, str] = Field(
        default={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        description="Default headers applied to every HTTP request.",
    )

    # -- storage ---------------------------------------------------------------
    export_dir: str = Field(
        default="./data/exports",
        description="Directory for CSV/Excel export files.",
    )

    # -- logging ---------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Log level for loguru (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )

    # -- instansi targets (codes from portal) ---------------------------------
    instansi_codes: list[str] = Field(
        default=DEFAULT_INSTANSI_CODES,
        description="List of instansi codes to crawl. Use INSTANSI_CODES for all 736.",
    )

    # -- helpers ---------------------------------------------------------------

    def get_instansi(self, kode: str) -> InstansiConfig:
        """Return an InstansiConfig for *kode*."""
        return InstansiConfig(kode=kode)

    def get_target_instansi(self) -> list[InstansiConfig]:
        """Return InstansiConfig objects for all configured codes."""
        return [InstansiConfig(kode=kode) for kode in self.instansi_codes]

    def with_all_instansi(self) -> Settings:
        """Return a copy of settings with all 736 instansi codes enabled."""
        self.instansi_codes = list(INSTANSI_CODES)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application Settings."""
    return Settings()
