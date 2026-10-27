// Модуль :core — чистый Kotlin/JVM без единого импорта android.*.
// Вся проверяемая без телефона логика живёт здесь.
plugins {
    id("org.jetbrains.kotlin.jvm")
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

// Windows + JDK: кириллица в пути проекта разваливает classpath
// тестового процесса Gradle (ClassNotFoundException на ровном месте —
// воспроизведено 20.09.2026). Каталог сборки :core уезжает в %TEMP%.
// Смотрим и на настоящий путь: через junction путь может быть ASCII, а файлы
// всё равно лежать в папке под агентом облачной синхронизации.
val realProjectPath: String = runCatching { projectDir.toPath().toRealPath().toString() }
    .getOrDefault(projectDir.absolutePath)
val projectPathHasNonAscii = (projectDir.absolutePath + realProjectPath).any { it.code > 127 }
val isWindows = System.getProperty("os.name").lowercase().contains("windows")
val tmpRoot = System.getProperty("java.io.tmpdir")
if (isWindows && projectPathHasNonAscii && !tmpRoot.isNullOrBlank() && tmpRoot.all { it.code < 128 }) {
    layout.buildDirectory.set(File(tmpRoot, "uzel-android-build/core"))
}

dependencies {
    // org.json на телефоне встроен в Android; на JVM — для компиляции и тестов.
    compileOnly("org.json:json:20240303")
    testImplementation("org.json:json:20240303")
    testImplementation("junit:junit:4.13.2")
}
