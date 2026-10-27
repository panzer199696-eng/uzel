// Модуль :app — то, что без телефона не проверить: BLE, служба
// переднего плана, экран на Compose (Material 3 Expressive).
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Каталог сборки :app тоже уезжает из проекта, и по двум причинам сразу:
// путь с кириллицей (та же беда, что у :core) и Google Drive, в бэкап
// которого заведён весь Рабочий стол — его агент держит файлы открытыми,
// и Gradle падает на `Unable to delete directory … app\build`
// (поймано 20.09.2026 на mergeDebugResources и на clean).
run {
    val realPath = runCatching { projectDir.toPath().toRealPath().toString() }
        .getOrDefault(projectDir.absolutePath)
    val projectPathHasNonAscii = (projectDir.absolutePath + realPath).any { it.code > 127 }
    val isWindows = System.getProperty("os.name").lowercase().contains("windows")
    val tmpRoot = System.getProperty("java.io.tmpdir")
    if (isWindows && projectPathHasNonAscii && !tmpRoot.isNullOrBlank() && tmpRoot.all { it.code < 128 }) {
        layout.buildDirectory.set(File(tmpRoot, "uzel-android-build/app"))
    }
}

android {
    namespace = "ru.uzel.bridge"
    compileSdk = 37

    defaultConfig {
        applicationId = "ru.uzel.bridge"
        minSdk = 26
        targetSdk = 37
        versionCode = 5
        versionName = "0.3.2"
    }

    buildTypes {
        // Ставится debug-сборка: она подписана тем же ключом, что и 0.1.0 на
        // телефоне, — обновление поверх сохраняет адрес, токен и спул.
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(project(":core"))

    val bom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(bom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    // 1.5 — Material 3 Expressive (действующий дизайн Google); BOM пока держит 1.4.
    implementation("androidx.compose.material3:material3:1.5.0-beta02")
    implementation("androidx.compose.material3.adaptive:adaptive:1.3.0")
    implementation("androidx.compose.material:material-icons-extended:1.7.8")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    // Обновление прошивки кулона по воздуху: Legacy DFU через загрузчик Adafruit
    implementation("no.nordicsemi.android:dfu:2.11.0")

    testImplementation("junit:junit:4.13.2")
}
