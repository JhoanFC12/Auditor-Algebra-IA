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
    estado_consistencia VARCHAR(50) DEFAULT 'Pendiente Revision', 
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