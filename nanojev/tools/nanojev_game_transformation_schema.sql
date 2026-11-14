-- NanoJev Game Transformation SQL schema v1; authoritative schema in nanojev_game_transformation_library.py

PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_references(ref_id TEXT PRIMARY KEY,url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS families(family_id TEXT PRIMARY KEY,label TEXT NOT NULL,math TEXT NOT NULL,tags TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS games(game_id TEXT PRIMARY KEY,family_id TEXT NOT NULL REFERENCES families(family_id),name TEXT NOT NULL,
   math TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('catalog_only','oracle_verified')),
   provenance TEXT NOT NULL,representation TEXT NOT NULL,parameters_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dictionary(term_id TEXT PRIMARY KEY,definition TEXT NOT NULL,math TEXT NOT NULL,role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transformations(op_id TEXT PRIMARY KEY,kind TEXT NOT NULL,definition TEXT NOT NULL,
   domain TEXT NOT NULL,claim TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('catalog_only','executable')));
CREATE TABLE IF NOT EXISTS game_relations(left_game TEXT NOT NULL REFERENCES games(game_id),right_game TEXT NOT NULL REFERENCES games(game_id),
   relation TEXT NOT NULL,conditions TEXT NOT NULL,PRIMARY KEY(left_game,right_game,relation));
CREATE TABLE IF NOT EXISTS training_objectives(objective_id TEXT PRIMARY KEY,full_objective_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS training_mix(mix_id TEXT NOT NULL, family_id TEXT NOT NULL REFERENCES families(family_id),
  examples_per_batch INTEGER NOT NULL CHECK(examples_per_batch>0),
  purpose TEXT NOT NULL, PRIMARY KEY(mix_id,family_id));
CREATE TABLE IF NOT EXISTS instances(instance_id TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(game_id),
   split TEXT NOT NULL CHECK(split IN ('train','dev','test')),kind TEXT NOT NULL,
   parameters_json TEXT NOT NULL,source_json TEXT NOT NULL,oracle_json TEXT NOT NULL,group_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS transformation_cases(case_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES instances(instance_id),
   op_id TEXT NOT NULL REFERENCES transformations(op_id),split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
   input_json TEXT NOT NULL,target_json TEXT NOT NULL,oracle_status TEXT NOT NULL CHECK(oracle_status='verified'));
CREATE TABLE IF NOT EXISTS presentations(presentation_id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES transformation_cases(case_id),
   orientation TEXT NOT NULL,prompt TEXT NOT NULL,target_json TEXT NOT NULL,
   UNIQUE(case_id,orientation));
CREATE INDEX IF NOT EXISTS idx_cases_split ON transformation_cases(split,op_id);
CREATE INDEX IF NOT EXISTS idx_instances_family ON instances(game_id,split);
CREATE INDEX IF NOT EXISTS idx_presentations_case ON presentations(case_id);
CREATE VIEW IF NOT EXISTS training_view AS SELECT c.case_id,c.split,i.game_id,c.op_id,p.orientation,p.prompt,p.target_json
   FROM transformation_cases c JOIN instances i ON c.instance_id=i.instance_id
   JOIN presentations p ON p.case_id=c.case_id;
