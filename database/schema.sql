"""
DEFINICIÓN DEL ESQUEMA DE LA BASE DE DATOS ALGEBRA_RAG
"""

-- Si usas pgvector, la extensión es necesaria.
CREATE EXTENSION IF NOT EXISTS vector;

-- Eliminar tablas existentes para una instalación limpia (solo si es necesario)
-- DROP TABLE IF EXISTS problema_reglas;
-- DROP TABLE IF EXISTS problemas;
-- DROP TABLE IF EXISTS reglas_matematicas; 

-- =============================================================================
-- TABLA DE REGLAS (TU BASE DE CONOCIMIENTO TEÓRICO)
-- =============================================================================
CREATE TABLE IF NOT EXISTS reglas_matematicas (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) UNIQUE NOT NULL,
    tipo VARCHAR(50),
    tema VARCHAR(150),
    condiciones_dominio TEXT, -- HIPOTESIS
    enunciado_formal_latex TEXT, -- CONCLUSION
    descripcion_pedagogica TEXT
);

-- =============================================================================
-- TABLA DE PROBLEMAS (EL NÚCLEO)
-- =============================================================================
CREATE TABLE IF NOT EXISTS problemas (
    -- IDENTIFICADORES Y ORIGEN
    id SERIAL PRIMARY KEY,
    numero_original INT NOT NULL,
    archivo_origen VARCHAR(255) NOT NULL,
    ruta_carpeta TEXT,
    fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- CONTENIDO
    enunciado_latex TEXT NOT NULL,
    respuesta VARCHAR(10), -- Clave (A, B, C...)

    -- CLASIFICACIÓN Y AUDITORÍA
    tema VARCHAR(150),
    nivel_dificultad VARCHAR(50),
    consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar',
    auditoria_razon TEXT, -- Justificación si está Mal Planteado

    -- SOLUCIONES Y CONOCIMIENTO RAG
    soluciones JSONB DEFAULT '[]'::jsonb, 
    -- [{"metodo_nombre": "Factorización", "solucion_latex": "...", "reglas_citadas": [1, 5]}]
    
    reglas_sugeridas_ia INTEGER[], -- [15]: IDs propuestos por la IA (Fase A)
    conceptos_ia JSONB DEFAULT '[]'::jsonb, -- Sin usar activamente en esta fase

    -- MOTOR DE BÚSQUEDA
    embedding VECTOR(1536), -- Vectorización del enunciado/solución

    -- RESTRICCIÓN
    CONSTRAINT unique_problema_origen UNIQUE (numero_original, archivo_origen)
);

-- Tabla de relaciones (Si se necesita un vínculo más flexible, aunque ahora usamos JSONB)
CREATE TABLE IF NOT EXISTS problema_reglas (
    problema_id INTEGER REFERENCES problemas(id),
    regla_id INTEGER REFERENCES reglas_matematicas(id),
    PRIMARY KEY (problema_id, regla_id)
);

-- =============================================================================
-- ORIGENES DE PROBLEMAS
-- =============================================================================
-- El problema conserva su contenido independiente. Esta capa indica de donde
-- salio: libro, examen de admision, simulacro, practica, separata, etc.
CREATE TABLE IF NOT EXISTS origenes (
    id SERIAL PRIMARY KEY,
    tipo_origen VARCHAR(50) NOT NULL DEFAULT 'general',
    codigo VARCHAR(160) NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    institucion TEXT NOT NULL DEFAULT '',
    anio INT,
    proceso VARCHAR(50) NOT NULL DEFAULT '',
    area VARCHAR(50) NOT NULL DEFAULT '',
    modalidad VARCHAR(120) NOT NULL DEFAULT '',
    proyecto TEXT NOT NULL DEFAULT '',
    libro TEXT NOT NULL DEFAULT '',
    instancia TEXT NOT NULL DEFAULT '',
    pdf_path TEXT NOT NULL DEFAULT '',
    session_path TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    estado VARCHAR(40) NOT NULL DEFAULT 'activo',
    notas TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS problema_origen (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    origen_id INT NOT NULL REFERENCES origenes(id) ON DELETE CASCADE,
    numero_original INT,
    orden INT,
    pagina INT,
    bloque TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, origen_id)
);

CREATE INDEX IF NOT EXISTS ix_origenes_tipo_codigo ON origenes(tipo_origen, codigo);
CREATE INDEX IF NOT EXISTS ix_problema_origen_origen ON problema_origen(origen_id);
CREATE INDEX IF NOT EXISTS ix_problema_origen_problema ON problema_origen(problema_id);

-- =============================================================================
-- CAPA SEMANTICA Y RELACIONAL PARA RECOMENDACION
-- =============================================================================
-- Estas tablas no reemplazan `problemas`. Guardan perfiles versionados y
-- vectores recalculables para busqueda por similitud, dificultad y diagnostico.

CREATE TABLE IF NOT EXISTS problema_assets (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    asset_type VARCHAR(40) NOT NULL,
    asset_tag VARCHAR(80) NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    content_hash VARCHAR(128) NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, asset_type, asset_tag, content_hash)
);

CREATE TABLE IF NOT EXISTS conceptos_matematicos (
    id SERIAL PRIMARY KEY,
    codigo VARCHAR(160) NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    curso VARCHAR(120) NOT NULL DEFAULT '',
    tema VARCHAR(160) NOT NULL DEFAULT '',
    tipo VARCHAR(60) NOT NULL DEFAULT 'concepto',
    descripcion TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    estado VARCHAR(40) NOT NULL DEFAULT 'pendiente',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS problema_concepto (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    concepto_id INT NOT NULL REFERENCES conceptos_matematicos(id) ON DELETE CASCADE,
    source VARCHAR(60) NOT NULL DEFAULT 'semantic_profile',
    role VARCHAR(60) NOT NULL DEFAULT 'concept',
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, concepto_id, role)
);

CREATE TABLE IF NOT EXISTS problem_semantic_profiles (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    schema_version VARCHAR(80) NOT NULL DEFAULT 'problem_semantic_profile_v1',
    profile_json JSONB NOT NULL,
    embedding_text TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
    human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, schema_version)
);

CREATE TABLE IF NOT EXISTS problem_figure_profiles (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    asset_id INT REFERENCES problema_assets(id) ON DELETE SET NULL,
    figure_tag VARCHAR(80) NOT NULL DEFAULT '',
    schema_version VARCHAR(80) NOT NULL DEFAULT 'geometry_figure_description_v1',
    profile_json JSONB NOT NULL,
    embedding_text TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
    human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, figure_tag, schema_version)
);

CREATE TABLE IF NOT EXISTS solution_semantic_profiles (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    solution_path_id VARCHAR(100) NOT NULL,
    schema_version VARCHAR(80) NOT NULL DEFAULT 'solution_semantic_profile_v1',
    solution_latex TEXT NOT NULL DEFAULT '',
    profile_json JSONB NOT NULL,
    embedding_text TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
    human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, solution_path_id, schema_version)
);

CREATE TABLE IF NOT EXISTS problem_embeddings (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    source_kind VARCHAR(60) NOT NULL,
    source_id INT,
    model_id TEXT NOT NULL,
    dimension INT NOT NULL,
    embedding VECTOR,
    embedding_text TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (problema_id, source_kind, source_id, model_id)
);

CREATE TABLE IF NOT EXISTS problem_similarity_edges (
    id SERIAL PRIMARY KEY,
    problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    similar_problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    score NUMERIC(7,6) NOT NULL,
    score_components JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
    human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CHECK (problema_id <> similar_problema_id),
    UNIQUE (problema_id, similar_problema_id, model_id)
);

CREATE TABLE IF NOT EXISTS semantic_practice_drafts (
    id SERIAL PRIMARY KEY,
    seed_problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
    schema_version VARCHAR(80) NOT NULL DEFAULT 'semantic_practice_draft_v1',
    title TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    draft_json JSONB NOT NULL,
    practice_latex TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'borrador',
    human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (seed_problema_id, schema_version, model_id)
);

CREATE INDEX IF NOT EXISTS ix_problema_assets_problem ON problema_assets(problema_id);
CREATE INDEX IF NOT EXISTS ix_conceptos_curso_tema ON conceptos_matematicos(curso, tema);
CREATE INDEX IF NOT EXISTS ix_problema_concepto_concepto ON problema_concepto(concepto_id);
CREATE INDEX IF NOT EXISTS ix_problem_semantic_profiles_problem ON problem_semantic_profiles(problema_id);
CREATE INDEX IF NOT EXISTS ix_problem_figure_profiles_problem ON problem_figure_profiles(problema_id);
CREATE INDEX IF NOT EXISTS ix_solution_semantic_profiles_problem ON solution_semantic_profiles(problema_id);
CREATE INDEX IF NOT EXISTS ix_problem_embeddings_problem_kind ON problem_embeddings(problema_id, source_kind);
CREATE INDEX IF NOT EXISTS ix_problem_similarity_edges_problem ON problem_similarity_edges(problema_id, score DESC);
CREATE INDEX IF NOT EXISTS ix_semantic_practice_drafts_seed ON semantic_practice_drafts(seed_problema_id, updated_at DESC);
